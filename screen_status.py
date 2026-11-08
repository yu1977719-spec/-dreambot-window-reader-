from pathlib import Path
import os
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


def read_task_panel_text(image):
    height, width = image.shape[:2]
    left = int(width * 0.78)
    top = int(height * 0.20)
    right = int(width * 0.99)
    bottom = int(height * 0.48)
    region = image[top:bottom, left:right]
    enlarged = cv2.resize(region, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)

    configured_tesseract = os.environ.get("TESSERACT_CMD", "").strip()
    if configured_tesseract:
        pytesseract.pytesseract.tesseract_cmd = configured_tesseract
    elif Path(TESSERACT_EXE).is_file():
        pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_EXE)
    return pytesseract.image_to_string(
        gray, lang="chi_sim+eng", config="--psm 6"
    ).strip()


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
