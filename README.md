# DreamBot Window Reader

一个 Windows 只读窗口识别小项目：按标题找到一个或多个窗口，截取可见窗口区域，在本机做场景匹配和 OCR；可选调用 DeepSeek 解释识别出的文字。

项目不点击、不按键、不控制游戏。默认云端请求只发送场景名和 OCR 文字；只有显式使用 `--vision` 时才会上传截图。

## 能做什么

- 按标题关键词筛选多个同名窗口，并用 HWND 和 PID 区分实例。
- 图形界面默认只识别当前最前面的一个窗口；用户可把游戏、微信或其他应用放到最前面再点识别。
- 微信窗口默认只 OCR 右侧聊天面板，排除左侧联系人列表，并尝试按发送者名称与下方文字整理；游戏场景分类只用于《梦幻西游》样本。
- 也可按标题筛选多个窗口；其他普通应用使用通用 OCR。
- 对每个窗口单独做本地 OCR 和场景识别，状态稳定变化后再输出。
- 可选使用 DeepSeek 解释场景和 OCR 文字。
- 场景样本、截图、录屏、日志都保留在本机；仓库忽略这些个人素材。

产品方向、当前边界和 WPS 报表导入计划见 [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md)。

## 环境

- Windows 10/11。
- Python 3.10 或更新版本。
- Tesseract OCR，并安装简体中文语言数据。默认查找 `C:\Program Files\Tesseract-OCR\tesseract.exe`；也可设置 `TESSERACT_CMD` 指向安装位置。

安装 Python 依赖：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 多窗口识别

启动简体中文图形界面：

```powershell
python window_app.py
```

界面默认选择“当前最前窗口”，只读取一个前台应用窗口，不会枚举后面的所有窗口；识别期间程序会暂时隐藏，截取刚才位于程序后面的最前窗口。将微信、游戏或其他目标程序放在最前面后点击“识别一次”即可。微信窗口默认只读取右侧聊天区域，尝试把检测到的发送者名称与其下方文字配对，跳过两侧头像区域，不读取左侧联系人列表，也会过滤聊天时间点；图片和表情内容无法靠 OCR 转成文字。也可选择“全桌面”或“按窗口标题”。普通应用不套用梦幻西游的场景样本；OCR 结果仍需人工核对。点击“查看上一次识别记录”可对照最近两次结果；记录只保存在当前程序会话中。点击“导出表格文件”会保存为 `.csv` 文件，可用 WPS 表格或 Excel 打开。微信导出列为会话、发送者、消息内容和消息状态，不包含窗口句柄等调试信息；当前仍是 OCR 结果，尚未直接写入 WPS 模板。

扫描标题含“梦幻西游”的所有可见窗口一次：

```powershell
python window_monitor.py --title "梦幻西游"
```

持续监视，只在状态稳定变化后输出：

```powershell
python window_monitor.py --title "梦幻西游" --watch --interval 3
```

标题关键词可换成其他应用的名称；当前 OCR 区域和场景样本针对《梦幻西游》手游界面，换目标应用时需要相应调整。`--all-windows` 会扫描全部可见窗口，请只在确认屏幕内容适合读取时使用。

截图来自桌面上窗口占用的矩形区域；窗口未最小化时，会裁取其落在显示器范围内的部分。若窗口被其他窗口遮挡，捕获到的画面也会包含遮挡内容。该工具不读取隐藏或最小化窗口。

需要读取所有显示器组成的整个桌面时，使用：

```powershell
python window_monitor.py --desktop
```

连续监视整个桌面并只在 OCR 内容稳定变化后输出：

```powershell
python window_monitor.py --desktop --watch --interval 3
```

全桌面模式只在本机执行 OCR，不调用云端；屏幕上的其他窗口内容也会被本机 OCR 读取。超宽或高分辨率画面会限制 OCR 缩放尺寸，避免生成过大的处理图。

## 添加本机场景样本

默认仓库不含游戏截图或其他私人素材。把本机参考截图放在 `screenshots/`，复制 `scene_samples.example.json` 为 `scene_samples.json`，再把示例路径替换为实际文件路径。`screenshots/` 和 `scene_samples.json` 已加入忽略列表，不会随常规 Git 添加操作上传。

没有场景样本时，程序仍可运行 OCR，并提示场景识别尚未配置。

## 按窗口设置 OCR 区域

默认 OCR 区域针对《梦幻西游》手游界面。要为不同应用或不同布局分别指定区域，复制 `window_profiles.example.json` 为本机的 `window_profiles.json`，按窗口标题关键词填写：

```json
{
  "profiles": [
    {
      "title_contains": "梦幻西游",
      "ocr_region": [0.78, 0.20, 0.99, 0.48]
    }
  ]
}
```

四个数依次表示左、上、右、下边界，占窗口宽高的比例，范围为 `0` 到 `1`。例如 `0.78` 表示距窗口左边 78% 的位置。程序按 profile 顺序使用第一个匹配的标题；未匹配的梦幻西游窗口沿用默认区域，其他窗口读取整个窗口。`window_profiles.json` 只保存在本机，不会提交。

## DeepSeek 可选解释

可运行下面的命令，通过剪贴板配置密钥：

```powershell
python ai_analyze.py --set-key
python ai_analyze.py --watch
```

`--set-key` 会读取剪贴板中的密钥，并写入当前 Windows 用户的环境设置（注册表中的用户级环境变量），不会写入项目文件。之后启动的程序会读取该设置。若剪贴板里没有密钥，脚本会退回到终端输入；此时输入内容会显示在屏幕上，因此建议先复制官方平台生成的密钥，再运行此命令。仓库不会保存密钥。

`--watch` 会监视匹配标题的所有窗口，每 25 秒最多发送一个稳定变化窗口的请求，默认只发送识别出的文字。使用 `python ai_analyze.py --watch --vision` 时，会把发生变化的窗口截图上传给 DeepSeek。

不要把 API Key 写进 `.py`、`.json`、`.env`、日志或公开仓库。

## 作为 Codex Skill 使用

本仓库根目录的 `SKILL.md` 是可复用的 Codex Skill。克隆仓库后，把整个目录复制到：

```text
%USERPROFILE%\.codex\skills\dreambot-window-reader
```

重启 Codex 后即可使用 `$dreambot-window-reader`。
