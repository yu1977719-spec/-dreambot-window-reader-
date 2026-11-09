from pathlib import Path
import os
import re
import sys

import cv2
import numpy as np
import pytesseract

from scene_detect import SCENE_CONFIG, SCENES, make_histogram


PROJECT_DIR = Path(__file__).resolve().parent
TESSERACT_EXE = os.environ.get("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
MIN_SCENE_SCORE = 0.65
MIN_SCORE_GAP = 0.10
DLY_TITLE_THRESHOLD = 0.80
STAR_MANUAL_THRESHOLD = 0.85
MAX_OCR_IMAGE_SIDE = 2400


def dly_title_score(image):
    """核对大雁塔弹窗顶部标题，避免仅凭颜色相似度误判。"""
    sample_path = SCENES["大雁塔弹窗"][0]
    sample = read_image(sample_path)
    # 固定窗口截图中，标题文字位于参考图的这个区域。
    template = sample[168:225, 580:700]
    if template.size == 0 or image.shape[0] < template.shape[0] or image.shape[1] < template.shape[1]:
        return 0.0
    image_gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    match = cv2.matchTemplate(image_gray, template_gray, cv2.TM_CCOEFF_NORMED)
    return cv2.minMaxLoc(match)[1]


def star_manual_score(image):
    """识别战斗界面左上角的“星宿手册”，优先区分二十八星宿战斗。"""
    sample_paths = SCENES.get("二十八星宿-战斗", [])
    if not sample_paths:
        return 0.0
    sample = read_image(sample_paths[0])
    template = sample[50:140, 345:466]
    if template.size == 0 or image.shape[0] < template.shape[0] or image.shape[1] < template.shape[1]:
        return 0.0
    image_gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    match = cv2.matchTemplate(image_gray, template_gray, cv2.TM_CCOEFF_NORMED)
    return cv2.minMaxLoc(match)[1]


def read_image(path):
    """用 imdecode 读图：cv2.imread 在 Windows 上打不开中文/空格路径。"""
    try:
        buffer = np.fromfile(str(path), dtype=np.uint8)
    except OSError as error:
        raise FileNotFoundError(f"无法读取图片：{path}（{error}）") from error
    if buffer.size == 0:
        raise FileNotFoundError(f"无法读取图片：{path}")
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"无法读取图片：{path}")
    return image


def find_scene(image):
    if not SCENES:
        return "未配置场景样本", 0.0, 0.0

    star_score = star_manual_score(image)
    if star_score >= STAR_MANUAL_THRESHOLD:
        return "二十八星宿-战斗", star_score, 1.0

    current_histogram = make_histogram(image)
    scores = []

    for scene_name, sample_paths in SCENES.items():
        if scene_name == "大雁塔弹窗" and dly_title_score(image) < DLY_TITLE_THRESHOLD:
            continue

        scene_scores = []
        for sample_path in sample_paths:
            sample_image = read_image(sample_path)
            sample_histogram = make_histogram(sample_image)
            score = cv2.compareHist(
                current_histogram, sample_histogram, cv2.HISTCMP_CORREL
            )
            scene_scores.append(score)
        scores.append((max(scene_scores), scene_name))

    scores.sort(reverse=True)
    best_score, best_scene = scores[0]
    second_score = scores[1][0] if len(scores) > 1 else -1.0
    gap = best_score - second_score

    if best_score < MIN_SCENE_SCORE or gap < MIN_SCORE_GAP:
        return "未知画面", best_score, gap
    return best_scene, best_score, gap


def read_task_panel_text(image, region=None, psm=6):
    height, width = image.shape[:2]
    if region is None:
        region = (0.78, 0.20, 0.99, 0.48)
    if len(region) != 4:
        raise ValueError("OCR 区域必须包含左、上、右、下四个归一化坐标。")
    left_ratio, top_ratio, right_ratio, bottom_ratio = region
    left = max(0, min(width, int(width * left_ratio)))
    top = max(0, min(height, int(height * top_ratio)))
    right = max(0, min(width, int(width * right_ratio)))
    bottom = max(0, min(height, int(height * bottom_ratio)))
    if left >= right or top >= bottom:
        raise ValueError("OCR 区域超出图像范围或宽高为零，请检查 window_profiles.json。")
    region = image[top:bottom, left:right]
    scale = min(2.0, MAX_OCR_IMAGE_SIDE / max(region.shape[:2]))
    if scale == 1.0:
        enlarged = region
    else:
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
        enlarged = cv2.resize(region, None, fx=scale, fy=scale, interpolation=interpolation)
    gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)

    configured_tesseract = os.environ.get("TESSERACT_CMD", "").strip()
    if configured_tesseract:
        pytesseract.pytesseract.tesseract_cmd = configured_tesseract
    elif Path(TESSERACT_EXE).is_file():
        pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_EXE)
    recognized_text = pytesseract.image_to_string(
        gray, lang="chi_sim+eng", config=f"--psm {psm}"
    )
    return "\n".join(line.strip() for line in recognized_text.splitlines() if line.strip())


def read_wechat_chat_text(image, region):
    """只读取微信右侧聊天面板，并按屏幕位置整理识别到的文字。"""
    height, width = image.shape[:2]
    left_ratio, top_ratio, right_ratio, bottom_ratio = region
    left = max(0, min(width, int(width * left_ratio)))
    top = max(0, min(height, int(height * top_ratio)))
    right = max(0, min(width, int(width * right_ratio)))
    bottom = max(0, min(height, int(height * bottom_ratio)))
    if left >= right or top >= bottom:
        raise ValueError("微信聊天区域无效，请检查窗口区域设置。")

    panel_width = right - left
    panel_height = bottom - top
    header_bottom = top + max(1, int(panel_height * 0.12))
    # 右上角的通话/菜单图标容易被 OCR 拼进会话名，标题只取左侧区域。
    header_right = left + max(1, int(panel_width * 0.55))
    header = image[top:header_bottom, left:header_right]
    body = image[header_bottom:bottom, left:right]

    configured_tesseract = os.environ.get("TESSERACT_CMD", "").strip()
    if configured_tesseract:
        pytesseract.pytesseract.tesseract_cmd = configured_tesseract
    elif Path(TESSERACT_EXE).is_file():
        pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_EXE)

    header_gray = cv2.cvtColor(header, cv2.COLOR_BGR2GRAY)
    header_text = pytesseract.image_to_string(
        header_gray, lang="chi_sim+eng", config="--psm 7"
    )
    header_text = " ".join(header_text.split())
    header_text = re.sub(r"[\s(（\[|]+$", "", header_text)
    if re.search(r"\(\d+$", header_text):
        header_text += ")"

    candidates = []
    body_gray = cv2.cvtColor(body, cv2.COLOR_BGR2GRAY)
    for source in (body, body_gray):
        data = pytesseract.image_to_data(
            source,
            lang="chi_sim+eng",
            config="--psm 11",
            output_type=pytesseract.Output.DICT,
        )
        for index, raw_text in enumerate(data["text"]):
            token_text = raw_text.strip()
            if not token_text:
                continue
            try:
                confidence = float(data["conf"][index])
            except (TypeError, ValueError):
                continue
            if confidence < 50 or not re.search(r"[A-Za-z0-9\u4e00-\u9fff@]", token_text):
                continue
            candidates.append(
                {
                    "text": token_text,
                    "left": int(data["left"][index]),
                    "top": int(data["top"][index]),
                    "width": max(1, int(data["width"][index])),
                    "height": max(1, int(data["height"][index])),
                    "confidence": confidence,
                }
            )

    def has_cjk(token_text):
        return bool(re.search(r"[\u4e00-\u9fff]", token_text))

    merged = []
    for candidate in sorted(candidates, key=lambda item: (item["top"], item["left"])):
        candidate_right = candidate["left"] + candidate["width"]
        candidate_bottom = candidate["top"] + candidate["height"]
        duplicate = None
        for existing in merged:
            existing_right = existing["left"] + existing["width"]
            existing_bottom = existing["top"] + existing["height"]
            overlap_width = max(
                0, min(candidate_right, existing_right) - max(candidate["left"], existing["left"])
            )
            overlap_height = max(
                0, min(candidate_bottom, existing_bottom) - max(candidate["top"], existing["top"])
            )
            overlap_area = overlap_width * overlap_height
            smaller_area = min(
                candidate["width"] * candidate["height"],
                existing["width"] * existing["height"],
            )
            if smaller_area and overlap_area / smaller_area >= 0.60:
                duplicate = existing
                break

        if duplicate is None:
            merged.append(candidate)
        elif candidate["text"] in duplicate["text"]:
            if len(candidate["text"]) > len(duplicate["text"]):
                duplicate.update(candidate)
        elif duplicate["text"] in candidate["text"]:
            if len(candidate["text"]) > len(duplicate["text"]):
                duplicate.update(candidate)
        elif has_cjk(candidate["text"]) != has_cjk(duplicate["text"]):
            if has_cjk(candidate["text"]):
                duplicate.update(candidate)
        elif candidate["confidence"] > duplicate["confidence"]:
            duplicate.update(candidate)

    merged.sort(key=lambda item: (item["top"] + item["height"] / 2, item["left"]))
    rows = []
    for token in merged:
        center_y = token["top"] + token["height"] / 2
        if rows and abs(center_y - rows[-1]["center_y"]) <= max(
            6, min(token["height"], rows[-1]["height"]) // 2 + 3
        ):
            row = rows[-1]
            row["tokens"].append(token)
            row["center_y"] = sum(
                item["top"] + item["height"] / 2 for item in row["tokens"]
            ) / len(row["tokens"])
            row["height"] = min(row["height"], token["height"])
        else:
            rows.append(
                {
                    "center_y": center_y,
                    "height": token["height"],
                    "left": token["left"],
                    "tokens": [token],
                }
            )

    def join_row(tokens):
        tokens.sort(key=lambda item: item["left"])
        parts = []
        previous = ""
        for token in tokens:
            current = token["text"]
            if parts and (
                (has_cjk(previous) and has_cjk(current))
                or previous == "@"
            ):
                parts[-1] += current
            else:
                parts.append(current)
            previous = current
        return " ".join(parts)

    chat_rows = []
    for row in rows:
        text = re.sub(r"\s+[@|]+$", "", join_row(row["tokens"])).strip()
        if text:
            row_left = min(token["left"] for token in row["tokens"])
            row_right = max(token["left"] + token["width"] for token in row["tokens"])
            row_center_x = (row_left + row_right) / 2
            possible_time = re.fullmatch(
                r"(?:(?:今天|昨天|天)\s*)?\d{1,2}(?::\d{2})?", text
            )
            centered_time = (
                possible_time is not None
                and abs(row_center_x - body.shape[1] / 2) <= body.shape[1] * 0.20
            )
            avatar_garble = row_left < body.shape[1] * 0.10 and not has_cjk(text)
            if centered_time or avatar_garble:
                continue
            chat_rows.append({"text": text, "top": row["center_y"], "left": row["left"]})

    output_rows = []
    index = 0
    while index < len(chat_rows):
        current = chat_rows[index]
        sender = current["text"]
        cjk_characters = re.sub(r"[^\u4e00-\u9fff]", "", sender)
        if (
            1 <= len(cjk_characters) <= 4
            and len(sender) <= 5
            and index + 1 < len(chat_rows)
        ):
            following = chat_rows[index + 1]
            vertical_gap = following["top"] - current["top"]
            horizontal_gap = abs(following["left"] - current["left"])
            if 0 < vertical_gap <= 55 and horizontal_gap <= 45:
                output_rows.append(f"{sender}：{following['text']}")
                index += 2
                continue
        output_rows.append(sender)
        index += 1

    timestamp_after_yesterday = re.compile(r"^天\s+(\d{1,2}:\d{2})$")
    output_rows = [
        "昨天" + match.group(1) if (match := timestamp_after_yesterday.match(line)) else line
        for line in output_rows
    ]
    timestamp_line = re.compile(r"^(?:(?:今天|昨天)\s*)?\d{1,2}:\d{2}$")
    output_rows = [line for line in output_rows if not timestamp_line.fullmatch(line.strip())]
    if header_text:
        output_rows.insert(0, f"当前会话：{header_text}")
    return "\n".join(output_rows)


def choose_status(scene_name, recognized_text):
    """分别判断背景场景和叠加界面，避免其中一个覆盖另一个。"""
    scene_statuses = {
        "未知画面": "未知画面",
        "世界地图": "世界地图",
        "大雁塔弹窗": "大雁塔弹窗",
        "背包界面": "背包界面",
        "空间动态页": "空间动态页",
        "个人空间主页": "个人空间主页",
        "副本-西海龙宫": "副本-西海龙宫",
        "副本-秘境降妖": "副本-秘境降妖",
        "战斗": "战斗画面",
        "宝图任务-战斗": "宝图任务战斗",
        "抓鬼任务-战斗": "抓鬼任务战斗",
        "二十八星宿-战斗": "二十八星宿战斗",
        "长安城": "长安城",
    }
    scene_status = scene_statuses.get(scene_name, scene_name)

    if "主线" in recognized_text or "社区" in recognized_text:
        overlay_status = "检测到任务面板文字"
    else:
        overlay_status = "未确认任务面板文字"

    return scene_status, overlay_status


def main():
    if len(sys.argv) != 2:
        print("用法：python screen_status.py 截图路径")
        return

    image_path = Path(sys.argv[1])
    try:
        image = read_image(image_path)
        scene_name, score, gap = find_scene(image)
        recognized_text = read_task_panel_text(image)
    except (FileNotFoundError, cv2.error, pytesseract.TesseractError) as error:
        print(f"处理失败：{error}")
        return

    scene_status, overlay_status = choose_status(scene_name, recognized_text)
    print(f"图片：{image_path}")
    print(f"场景判断：{scene_name}（相似度={score:.3f}，分差={gap:.3f}）")
    print(f"OCR 关键词：{'主线或社区' if ('主线' in recognized_text or '社区' in recognized_text) else '未确认'}")
    print(f"基础场景：{scene_status}")
    print(f"界面状态：{overlay_status}")


if __name__ == "__main__":
    main()
