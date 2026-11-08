"""把本机 OCR 结果或截图交给 DeepSeek 云端模型解释。

用法：
  python ai_analyze.py 截图路径            本机 OCR，只把文字发到云端（不上传图片）
  python ai_analyze.py --vision 截图路径   截图压缩后上传，由 deepseek-flash 直接读图
  python ai_analyze.py --chat              交互式对话，可随时 /截图 发一张图给云端
  python ai_analyze.py --watch             盯着游戏窗口，画面变化时自动问云端（只发文字）
  python ai_analyze.py --watch --vision    同上，但每次上传截图，由云端直接读图
  python ai_analyze.py --set-key           把密钥写进用户环境变量（隐藏输入）
  python ai_analyze.py --test              纯文本连通测试
  python ai_analyze.py --diag              逐层诊断：DNS → TLS → 代理 → 密钥 → 接口
  python ai_analyze.py --list-models       用当前密钥列出可用模型

本脚本不执行任何游戏操作。
"""

import base64
import json
import os
import socket
import ssl
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import httpx
import numpy as np
from openai import OpenAI

from screen_status import find_scene, read_image, read_task_panel_text
from window_profiles import load_profiles, resolve_ocr_region


BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")

# 系统代理有时会指向一个没在运行的本机端口（ProxyEnable=1 但 7888 没人监听），
# httpx/urllib 会照用不误，结果所有请求都是 WinError 10061。这里让本服务直连。
for _proxy_name in ("NO_PROXY", "no_proxy"):
    _current = os.environ.get(_proxy_name, "")
    if "api.deepseek.com" not in _current:
        os.environ[_proxy_name] = ",".join(part for part in (_current, "api.deepseek.com") if part)

FALLBACK_MODELS = ("deepseek-chat",)
# 实测 api.deepseek.com 前面的 CDN（Tencent EdgeOne）会间歇性把稍大的请求打成 400/550，
# 或直接断连；同一请求重发往往就成功了。所以这里既做重试，也把图片按宽度逐级降档。
VISION_ATTEMPTS = ((1280, 82), (1024, 78), (900, 74), (800, 72), (640, 65), (512, 60))
VISION_BUDGET_BYTES = 200 * 1024
MAX_ATTEMPTS = 3
RETRY_DELAYS = (0.8, 2.0)
RETRYABLE_STATUS = (408, 500, 502, 503, 504, 550)
SCREENSHOT_DIR = Path(__file__).resolve().parent / "screenshots"
PUBLIC_CA_HINTS = ("TrustAsia", "DigiCert", "Let's Encrypt", "GlobalSign", "Sectigo", "GeoTrust")


def check_api_key(raw_key):
    """拦掉复制粘贴最常见的坏输入，避免把格式错误发到服务端变成 400。"""
    key = (raw_key or "").strip()
    if not key:
        raise ValueError("没有输入 API 密钥。")
    if not key.isascii():
        raise ValueError(
            "API Key 里含非 ASCII 字符（多半是复制时带进了中文标点、全角字符，或者这里还是占位文字）。"
            "请用  python ai_analyze.py --set-key  重新写入真实密钥。"
        )
    if any(character.isspace() for character in key):
        raise ValueError(
            "API Key 里混入了空格或换行（粘贴时容易带上），请只复制 sk- 开头的那一串。"
        )
    if not key.startswith("sk-") or len(key) < 20:
        raise ValueError(
            f"这串不是完整密钥（长度只有 {len(key)}，完整密钥应以 sk- 开头且明显更长）。"
            "隐藏输入里按 Ctrl+V，有些终端只会送进一个控制字符；"
            "请重新运行命令，用“从剪贴板读取”的方式粘贴。"
        )
    return key


def mask_key(key):
    return f"{key[:8]}****{key[-4:]}" if len(key) > 12 else "****"


def read_clipboard_text():
    """读 Windows 剪贴板，绕开隐藏输入里 Ctrl+V 只送进一个字符的问题。"""
    if os.name != "nt":
        return ""
    try:
        import ctypes

        CF_UNICODETEXT = 13
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(0):
            return ""
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return ctypes.wstring_at(pointer).strip()
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except Exception:
        return ""


def prompt_for_key():
    """先看剪贴板（最省事），不行再明着输入；不用 getpass，避免粘贴失效。"""
    clipboard = read_clipboard_text()
    if clipboard.startswith("sk-") and len(clipboard) >= 20:
        print(f"剪贴板里发现密钥：{mask_key(clipboard)}")
        answer = input("直接使用这一串？[Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            return check_api_key(clipboard)
    return check_api_key(input("DeepSeek API Key（粘贴后会显示在屏幕上）："))


def read_windows_user_env(name):
    """读 HKCU\\Environment：setx 写进去的值，当前进程不用重启也能读到。"""
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
            value, _ = winreg.QueryValueEx(handle, name)
            return str(value).strip()
    except OSError:
        return ""


def write_windows_user_env(name, value):
    """写入用户环境变量，并广播变更，让之后新开的程序立刻能读到。"""
    if os.name != "nt":
        raise RuntimeError("只有 Windows 支持写入用户环境变量。")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as handle:
        winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)
    try:
        import ctypes

        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)
    except Exception:
        pass


def read_windows_proxy():
    """读系统代理（WinINET），返回 (是否启用, 地址)。代理坏了会表现成连接被拒。"""
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as handle:
            enabled = bool(winreg.QueryValueEx(handle, "ProxyEnable")[0])
            server = str(winreg.QueryValueEx(handle, "ProxyServer")[0])
            return enabled, server
    except OSError:
        return None


def get_api_key():
    """优先读环境变量（进程 → 用户级），都没有再交互输入；不写项目文件。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        key = read_windows_user_env("DEEPSEEK_API_KEY")
    if not key:
        print("没有找到 DEEPSEEK_API_KEY 环境变量。")
        key = prompt_for_key()
    return check_api_key(key)


def set_key():
    """把密钥写进用户环境变量：优先从剪贴板读，避免隐藏输入粘贴失败。"""
    print("请先在 platform.deepseek.com/api_keys 页面点“复制”，把完整密钥放进剪贴板。")
    key = prompt_for_key()
    write_windows_user_env("DEEPSEEK_API_KEY", key)
    os.environ["DEEPSEEK_API_KEY"] = key
    print(f"已写入用户环境变量 DEEPSEEK_API_KEY：{mask_key(key)}（长度 {len(key)}）")

    status = 0
    print("正在验证这把密钥……")
    try:
        response = httpx.get(
            f"{BASE_URL}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=20.0,
        )
    except Exception as error:
        print(f"验证没连上（{type(error).__name__}），密钥已保存，稍后用 --diag 再查。")
    else:
        if response.status_code == 200:
            names = [item.get("id", "?") for item in response.json().get("data", [])]
            print(f"验证通过，可用模型：{'、'.join(names) or '（列表为空）'}")
        elif response.status_code == 401:
            print("验证失败：平台说这把密钥无效（401）。请确认复制的是完整密钥，或重新创建一个。")
            status = 1
        else:
            print(f"验证返回 HTTP {response.status_code}：{response.text[:200]}")

    print("下一步：python ai_analyze.py --diag")
    return status


def error_detail(error):
    """从 SDK 异常里抠出状态码、响应头和服务端原文。"""
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    headers = {}
    detail = ""
    if response is not None:
        headers = dict(getattr(response, "headers", {}) or {})
        try:
            detail = response.text or ""
        except Exception:
            detail = ""
    if not detail:
        body = getattr(error, "body", None)
        if isinstance(body, (dict, list)):
            detail = json.dumps(body, ensure_ascii=False)
        elif body is not None:
            detail = str(body)
    return status_code, headers, detail


def request_chat(api_key, messages):
    """调用对话接口。

    api.deepseek.com 前面的 CDN 会间歇性返回 400/550 或直接断开连接，同一请求重发就正常，
    所以这里对“看起来像链路问题”的错误重试；模型名被拒时再退到备用模型。
    """
    models = [MODEL] + [name for name in FALLBACK_MODELS if name != MODEL]
    client = OpenAI(api_key=api_key, base_url=BASE_URL, max_retries=0, timeout=90.0)
    last_error = None
    for model in models:
        for attempt in range(MAX_ATTEMPTS):
            try:
                return client.chat.completions.create(model=model, messages=messages, stream=False)
            except Exception as error:
                last_error = error
                status_code = getattr(error, "status_code", None)
                retryable = status_code is None or status_code in RETRYABLE_STATUS
                if status_code == 400 and attempt == 0:
                    retryable = True
                if retryable and attempt < MAX_ATTEMPTS - 1:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    print(
                        f"  第 {attempt + 1} 次请求失败"
                        f"（{status_code or type(error).__name__}），{delay:.1f} 秒后重试……"
                    )
                    time.sleep(delay)
                    continue
                break
        status_code = getattr(last_error, "status_code", None)
        if status_code in (400, 404) and model != models[-1]:
            print(f"模型 {model} 被拒（HTTP {status_code}），改用 {models[-1]} 重试……")
            continue
        raise last_error
    raise last_error


def explain_http_error(error):
    """把 HTTP 错误翻译成能直接照做的下一步。"""
    status_code, headers, detail = error_detail(error)
    if status_code == 401:
        return "DeepSeek API Key 无效或未启用；请到 platform.deepseek.com 的 API keys 页确认密钥状态。"
    if status_code == 402:
        return "账户余额不足，请到开放平台充值后再试。"
    if status_code == 429:
        return "请求过多或额度受限，稍等一会儿再试。"
    if status_code == 400:
        return (
            "服务端判定请求格式错误（HTTP 400）。常见原因：密钥里混入空格或换行、模型名不对、"
            "请求体过大，或者本机代理/加速器改写了 HTTPS 请求。\n"
            "下一步：先运行  python ai_analyze.py --diag  看清是哪一层拒绝的。"
            f"\n服务端原文：{detail[:400] or '（空）'}"
        )
    if status_code in (413, 550):
        return (
            f"请求体在 CDN/WAF 层就被拒了（HTTP {status_code}，"
            f"Server={headers.get('server', '未提供')}）。图片太大是常见原因，"
            "脚本会自动降档重试；仍然失败就把截图裁小一些。"
        )
    if status_code:
        trace_id = (
            headers.get("x-ds-trace-id")
            or headers.get("x-request-id")
            or headers.get("request-id")
            or "未提供"
        )
        return (
            f"云端请求失败（HTTP {status_code}）。"
            f"请求编号：{trace_id}；服务器：{headers.get('server', '未提供')}；"
            f"响应类型：{headers.get('content-type', '未提供')}；"
            f"响应长度：{headers.get('content-length', '未知')}；"
            f"接口：{BASE_URL}/chat/completions；模型：{MODEL}\n"
            f"服务端原文：{detail[:800] or '（空）'}"
        )
    cause = error.__cause__ or error.__context__
    text = f"连接云端失败：{type(error).__name__}: {error}"
    if cause is not None and cause is not error:
        text += f"；底层原因：{type(cause).__name__}: {cause}"
    proxy = {
        name: value
        for name, value in os.environ.items()
        if name.lower() in ("http_proxy", "https_proxy", "all_proxy")
    }
    if proxy:
        text += f"；当前代理变量：{proxy}（可先 set NO_PROXY=api.deepseek.com 再试）"
    windows_proxy = read_windows_proxy()
    if windows_proxy and windows_proxy[0]:
        text += (
            f"；系统代理已开启（{windows_proxy[1]}），如果这个端口没有程序在监听，"
            "所有请求都会报 WinError 10061；去「设置 → 网络和 Internet → 代理」关掉它，"
            "或给 api.deepseek.com 加直连例外"
        )
    return text + f"；接口：{BASE_URL}/chat/completions"


def encode_image(image, max_width, quality):
    """把截图缩到指定宽度并压成 JPEG，减少上传体积和 token。"""
    height, width = image.shape[:2]
    if width > max_width:
        scale = max_width / width
        image = cv2.resize(image, (max_width, int(height * scale)), interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("截图编码失败，无法上传。")
    data = base64.b64encode(buffer.tobytes()).decode("ascii")
    return data, image.shape[1], image.shape[0], len(buffer) / 1024


def vision_chat(api_key, image, prompt, history=None):
    """把图片和文字作为一条 user 消息发出去；请求体被 CDN/WAF 拒掉时自动降档重试。"""
    history = list(history or [])
    total = len(VISION_ATTEMPTS)
    # 先挑第一个塞得进预算的档位，避免一上来就发 300 KB 的请求去撞 CDN。
    start = total - 1
    for index, (width, quality) in enumerate(VISION_ATTEMPTS):
        probe, _, _, _ = encode_image(image, width, quality)
        if len(probe) <= VISION_BUDGET_BYTES:
            start = index
            break

    last_error = None
    for index in range(start, total):
        width, quality = VISION_ATTEMPTS[index]
        data, real_width, real_height, size_kb = encode_image(image, width, quality)
        print(
            f"上传截图：{real_width}×{real_height}，JPEG 质量 {quality}，"
            f"压缩后 {size_kb:.0f} KB，base64 后 {len(data) / 1024:.0f} KB"
        )
        messages = history + [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{data}",
                            "detail": "high",
                        },
                    },
                ],
            }
        ]
        try:
            response = request_chat(api_key, messages)
        except Exception as error:
            last_error = error
            status_code = getattr(error, "status_code", None)
            if status_code in (400, 413, 500, 550) and index < total - 1:
                print(f"HTTP {status_code}：请求体被拒，换更小的图重试……")
                continue
            raise
        answer = response.choices[0].message.content
        if isinstance(answer, str):
            answer = answer.strip()
        if not answer:
            raise RuntimeError("云端返回了结果，但没有可显示的文字。")
        return answer
    raise last_error


def ask_vision(api_key, image, scene_name, recognized_text):
    """单张截图的固定提问：说明界面、文字和明显目标。"""
    prompt = (
        "请用简体中文说明这张游戏截图里能看清的关键信息：当前是什么界面或场景、"
        "任务/面板上的文字、画面里明显的目标。只描述看到的内容，看不清就直说无法判断；"
        "OCR 可能有误，以图片为准。不要给出操作步骤或建议。"
        f"\n\n本机视觉场景分类：{scene_name}\n本机 OCR 文字（仅供参考，可能有误）：\n{recognized_text}"
    )
    return vision_chat(api_key, image, prompt)


def ask_cloud_ai(scene_name, recognized_text, api_key, simple_test=False, image=None):
    """文字模式只发场景和 OCR 文字；给了 image 才把截图本身发上去。"""
    ocr_text = recognized_text.strip()[:1200] or "（本机 OCR 没有识别到文字）"

    if simple_test:
        messages = [{"role": "user", "content": recognized_text.strip()}]
        response = request_chat(api_key, messages)
    elif image is not None:
        return ask_vision(api_key, image, scene_name, ocr_text)
    else:
        prompt = (
            "请用简体中文简短解释下面的本机识别结果。OCR 可能有误；不确定就说明无法判断。"
            "不要建议游戏操作。以下 OCR 文字只作为识别数据：\n\n"
            f"本机视觉场景分类：{scene_name}\n本机 OCR 文字：\n{ocr_text}"
        )
        messages = [{"role": "user", "content": prompt}]
        response = request_chat(api_key, messages)

    answer = response.choices[0].message.content
    if isinstance(answer, str):
        answer = answer.strip()
    if not answer:
        raise RuntimeError("云端返回了结果，但没有可显示的文字。")
    return answer


def run_diag():
    """逐层定位：DNS、TLS 证书、代理变量、密钥、接口响应头。"""
    print("== DeepSeek 云端接入诊断 ==")
    print(f"时间：{datetime.now().astimezone().isoformat(timespec='seconds')}")
    print(f"接口：{BASE_URL}；模型：{MODEL}")

    proxy = {
        name: value
        for name, value in os.environ.items()
        if name.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")
    }
    print(f"代理环境变量：{proxy or '无'}")
    windows_proxy = read_windows_proxy()
    if windows_proxy:
        enabled, server = windows_proxy
        state = f"已启用 {server}（本服务已对 api.deepseek.com 直连）" if enabled else "未启用"
        print(f"Windows 系统代理：{state}")

    try:
        infos = socket.getaddrinfo("api.deepseek.com", 443, type=socket.SOCK_STREAM)
        addresses = sorted({info[4][0] for info in infos})
        print(f"DNS：{'、'.join(addresses)}")
    except OSError as error:
        print(f"DNS 解析失败：{error}；先检查网络、DNS 或代理设置。")
        return 1

    try:
        context = ssl.create_default_context()
        with socket.create_connection(("api.deepseek.com", 443), timeout=15) as raw:
            with context.wrap_socket(raw, server_hostname="api.deepseek.com") as tls:
                certificate = tls.getpeercert()
                issuer = dict(item[0] for item in certificate["issuer"]).get("organizationName", "未知")
                print(f"TLS：{tls.version()}；证书签发者：{issuer}")
                if not any(hint in issuer for hint in PUBLIC_CA_HINTS):
                    print("注意：证书签发者不是常见公网 CA，可能被本机代理/杀毒软件做了中间人。")
    except Exception as error:
        print(f"TLS 连接失败：{type(error).__name__}: {error}")
        return 1

    try:
        api_key = get_api_key()
    except ValueError as error:
        print(f"密钥检查未通过：{error}")
        return 1

    try:
        response = httpx.get(
            f"{BASE_URL}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )
    except Exception as error:
        print(f"HTTP 连接失败：{type(error).__name__}: {error}")
        return 1

    print(
        f"GET /models → HTTP {response.status_code}；"
        f"Server={response.headers.get('server', '未提供')}；"
        f"Content-Type={response.headers.get('content-type', '未提供')}；"
        f"Trace={response.headers.get('x-ds-trace-id', '未提供')}"
    )

    if response.status_code == 200:
        names = [item.get("id", "?") for item in response.json().get("data", [])]
        print(f"密钥有效。可用模型：{'、'.join(names) or '（列表为空）'}")
        if MODEL not in names:
            print(f"注意：当前模型 {MODEL} 不在列表里，脚本会自动改用 {FALLBACK_MODELS[0]}。")
        print("结论：链路正常，可以直接运行  python ai_analyze.py --vision 截图路径")
        return 0
    if response.status_code == 401:
        print("结论：密钥无效或已停用，请到 platform.deepseek.com 重新创建。")
        return 1
    if response.status_code == 400:
        print("结论：请求在服务端被判定为格式错误，通常是客户端或中间代理改写了请求头。")
        print(f"服务端原文：{response.text[:400]}")
        return 1
    print(f"结论：接口返回 {response.status_code}；服务端原文：{response.text[:400]}")
    return 1


def list_models():
    client = OpenAI(api_key=get_api_key(), base_url=BASE_URL, max_retries=0, timeout=30.0)
    models = client.models.list()
    print("此密钥可用的模型 ID：")
    for item in models.data:
        print(f"  {item.id}")
    return 0


def analyze_one(image_path, use_vision=False):
    """分析单张截图：本机场景 + OCR，再按开关决定要不要把图片本身发上去。"""
    try:
        image = read_image(image_path)
        scene_name, score, gap = find_scene(image)
        recognized_text = read_task_panel_text(image)
        print(f"本机场景判断：{scene_name}（相似度={score:.3f}，分差={gap:.3f}）")
        print(f"本机 OCR：{recognized_text or '（没有识别到文字）'}")
        if use_vision:
            print("正在把截图和上述文字上传到 DeepSeek；图片会离开本机，请确认画面里没有隐私信息。")
        else:
            print(f"正在通过 DeepSeek 的 {MODEL} 发送场景名称和 OCR 文字；截图不会上传。")
        answer = ask_cloud_ai(
            scene_name,
            recognized_text,
            get_api_key(),
            image=image if use_vision else None,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as error:
        print(f"处理失败：{error}")
        return 1
    except Exception as error:
        print(f"处理失败：{explain_http_error(error)}")
        return 1

    print("\n云端读图结果：" if use_vision else "\n云端 AI 解释：")
    print(answer)
    return 0


def run_watch(use_vision=False):
    """持续监视所有匹配窗口：每次仅把一个稳定变化的窗口送给云端。"""
    from window_capture import capture_window, find_windows

    check_interval = 3.0
    confirm_readings = 2
    cooldown_seconds = 25.0

    try:
        profiles = load_profiles()
    except ValueError as error:
        print(f"窗口配置有误：{error}")
        print("请修正 window_profiles.json，或先将它移走以使用默认 OCR 区域。")
        return 2

    try:
        api_key = get_api_key()
    except ValueError as error:
        print(f"处理失败：{error}")
        return 1

    mode = "截图上传，由云端读图" if use_vision else "只发 OCR 文字，不上传截图"
    print(f"开始监视游戏窗口并接入 DeepSeek；模式：{mode}；按 Ctrl+C 停止。")

    window_states = {}
    last_cloud_at = 0.0

    try:
        while True:
            targets = find_windows(WINDOW_TITLE_KEYWORD)
            if not targets:
                print("没有找到游戏窗口，请确认它已打开。")
                time.sleep(check_interval)
                continue

            active_keys = {(target.hwnd, target.pid) for target in targets}
            for key in list(window_states):
                if key not in active_keys:
                    window_states.pop(key, None)

            candidates = []
            for target in targets:
                key = (target.hwnd, target.pid)
                state = window_states.setdefault(
                    key,
                    {"pending": None, "count": 0, "previous": None},
                )
                try:
                    screenshot = capture_window(target.hwnd)
                    image = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
                    scene_name, score, gap = find_scene(image)
                    recognized_text = read_task_panel_text(
                        image,
                        region=resolve_ocr_region(target.title, profiles),
                    )
                except Exception as error:
                    print(
                        f"读取窗口 {target.title!r}（HWND={target.hwnd}）失败，"
                        f"{check_interval:.0f} 秒后重试：{error}"
                    )
                    continue

                signature = (scene_name, recognized_text.strip()[:200])
                if signature == state["pending"]:
                    state["count"] += 1
                else:
                    state["pending"] = signature
                    state["count"] = 1

                if state["count"] >= confirm_readings and signature != state["previous"]:
                    candidates.append(
                        (target, state, image, scene_name, score, recognized_text, signature)
                    )

            now = time.monotonic()
            if candidates and now - last_cloud_at >= cooldown_seconds:
                target, state, image, scene_name, score, recognized_text, signature = candidates[0]
                stamp = datetime.now().strftime("%H:%M:%S")
                print(
                    f"[{stamp}] 窗口={target.title!r}；HWND={target.hwnd}；PID={target.pid}；"
                    f"场景={scene_name}（相似度={score:.3f}）；调用云端……"
                )
                try:
                    answer = ask_cloud_ai(
                        scene_name,
                        recognized_text,
                        api_key,
                        image=image if use_vision else None,
                    )
                    print(answer)
                except Exception as error:
                    print(f"云端调用失败：{explain_http_error(error)}")
                state["previous"] = signature
                last_cloud_at = time.monotonic()

            time.sleep(check_interval)
    except KeyboardInterrupt:
        print("监视已停止。")
    return 0


def capture_current_window():
    """截一张标题匹配的游戏窗口当前画面（BGR）。"""
    from window_capture import capture_window, find_windows

    windows = find_windows(WINDOW_TITLE_KEYWORD)
    if not windows:
        raise RuntimeError("没有找到游戏窗口，请确认它已打开。")
    return cv2.cvtColor(np.array(capture_window(windows[0].hwnd)), cv2.COLOR_RGB2BGR)


def chat_reply(api_key, messages):
    """流式输出，边收边打；流被掐断时退回普通请求（自带重试）。"""
    client = OpenAI(api_key=api_key, base_url=BASE_URL, max_retries=0, timeout=120.0)
    print("AI> ", end="", flush=True)
    text = ""
    try:
        stream = client.chat.completions.create(model=MODEL, messages=messages, stream=True)
        for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            piece = choice.delta.content if choice and choice.delta else None
            if piece:
                text += piece
                print(piece, end="", flush=True)
        print()
    except Exception:
        if text:
            print()
            raise
        print("（流式连接失败，改用普通请求）")
        response = request_chat(api_key, messages)
        text = (response.choices[0].message.content or "").strip()
        print(f"AI> {text}")
    return text.strip()


CHAT_HELP = """可用命令：
  /截图 [问题]     抓取当前游戏窗口发给云端，可附一句你想问的
  /图 路径 [问题]  发送指定图片
  /清空            清空上下文，重新开始
  /退出            结束对话
也可以直接输入文字提问，Ctrl+C 退出。"""


def run_chat():
    """交互式对话：保留上下文，随时可以丢一张截图进去。"""
    try:
        api_key = get_api_key()
    except ValueError as error:
        print(f"处理失败：{error}")
        return 1

    print("已进入对话模式。输入 /帮助 查看命令，/退出 结束。")
    system = {
        "role": "system",
        "content": (
            "你是《梦幻西游》游戏画面的解读助手。用简体中文、简洁地回答。"
            "只描述你能确定看到的内容；看不清或不知道就直说，不要编造游戏机制或操作步骤。"
        ),
    }
    history = [system]

    while True:
        try:
            line = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出对话。")
            return 0

        if not line:
            continue

        command, _, rest = line.partition(" ")
        command = command.lower()
        rest = rest.strip()

        if command in ("/exit", "/quit", "/退出", "退出", "exit", "quit"):
            print("已退出对话。")
            return 0
        if command in ("/help", "/帮助"):
            print(CHAT_HELP)
            continue
        if command in ("/clear", "/清空"):
            history = [system]
            print("上下文已清空。")
            continue

        try:
            if command in ("/shot", "/截图"):
                image = capture_current_window()
                question = rest or "请说明这张截图里的界面、文字和明显目标。"
                answer = vision_chat(api_key, image, question, history)
                print(f"\nAI> {answer}")
                history.append({"role": "user", "content": question})
                history.append({"role": "assistant", "content": answer})
                continue
            if command in ("/img", "/图"):
                if not rest:
                    print("用法：/图 截图路径 [问题]")
                    continue
                path_text, _, question = rest.partition(" ")
                image = read_image(Path(path_text))
                question = question.strip() or "请说明这张图片里的内容。"
                answer = vision_chat(api_key, image, question, history)
                print(f"\nAI> {answer}")
                history.append({"role": "user", "content": question})
                history.append({"role": "assistant", "content": answer})
                continue
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            print(f"\n失败：{error}")
            continue
        except Exception as error:
            print(f"\n失败：{explain_http_error(error)}")
            continue

        history.append({"role": "user", "content": line})
        try:
            answer = chat_reply(api_key, history)
        except Exception as error:
            history.pop()
            print(f"\n调用失败：{explain_http_error(error)}")
            continue
        if answer:
            history.append({"role": "assistant", "content": answer})
        if len(history) > 17:
            history = [history[0]] + history[-16:]


def main():
    arguments = sys.argv[1:]
    use_vision = "--vision" in arguments
    if use_vision:
        arguments = [item for item in arguments if item != "--vision"]

    if arguments == ["--set-key"]:
        return set_key()

    if arguments == ["--diag"]:
        return run_diag()

    if arguments == ["--list-models"]:
        try:
            return list_models()
        except Exception as error:
            print(f"模型列表读取失败：{explain_http_error(error)}")
            return 1

    if arguments == ["--test"]:
        try:
            occurred_at = datetime.now().astimezone().isoformat(timespec="seconds")
            print(f"正在通过 DeepSeek 做纯文本连通测试；模型：{MODEL}；不读取或上传游戏截图/OCR。")
            print(f"请求时间（本机时区）：{occurred_at}")
            print(f"请求接口：POST {BASE_URL}/chat/completions")
            print(f"请求参数：model={MODEL}；messages=1 条纯文本 user 消息；stream=false；未发送截图")
            answer = ask_cloud_ai(
                "连接测试",
                "这是纯文本连通测试，请回复：连接成功",
                get_api_key(),
                simple_test=True,
            )
        except ValueError as error:
            print(f"处理失败：{error}")
            return 1
        except Exception as error:
            print(f"处理失败：{explain_http_error(error)}")
            return 1
        print(f"云端返回：{answer}")
        return 0

    if arguments == ["--chat"]:
        return run_chat()

    if arguments == ["--watch"]:
        return run_watch(use_vision)

    if len(arguments) == 1:
        return analyze_one(Path(arguments[0]), use_vision)

    print("用法：python ai_analyze.py 截图路径")
    print("读图：python ai_analyze.py --vision 截图路径")
    print("对话：python ai_analyze.py --chat")
    print("实时接入：python ai_analyze.py --watch [--vision]")
    print("写入密钥：python ai_analyze.py --set-key")
    print("连通测试：python ai_analyze.py --test")
    print("诊断：python ai_analyze.py --diag")
    print("查看可用模型：python ai_analyze.py --list-models")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
