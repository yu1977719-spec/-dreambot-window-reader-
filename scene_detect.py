from pathlib import Path
import json
import sys

import cv2
import numpy as np


PROJECT_DIR = Path(__file__).resolve().parent
SCENE_CONFIG = PROJECT_DIR / "scene_samples.json"
if SCENE_CONFIG.exists():
    with SCENE_CONFIG.open("r", encoding="utf-8") as config_file:
        scene_data = json.load(config_file)
else:
    scene_data = {}
SCENES = {
    scene_name: [PROJECT_DIR / sample_path for sample_path in sample_paths]
    for scene_name, sample_paths in scene_data.items()
    if isinstance(sample_paths, list) and sample_paths
}


def read_image(path):
    try:
        image_data = np.fromfile(str(path), dtype=np.uint8)
    except OSError as error:
        raise FileNotFoundError(f"无法读取图片：{path}（{error}）") from error
    if image_data.size == 0:
        raise FileNotFoundError(f"无法读取图片：{path}")
    image = cv2.imdecode(image_data, cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"无法读取图片：{path}")
    return image


def make_histogram(image):
    """比较画面中央区域的 HSV 颜色分布，减少窗口边缘的干扰。"""
    height, width = image.shape[:2]
    # 重点看右半边游戏区域，降低左侧聊天/帮派窗口展开时的干扰。
    image = image[int(height * 0.15):int(height * 0.88), int(width * 0.50):int(width * 0.96)]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
    cv2.normalize(histogram, histogram)
    return histogram


def main():
    if len(sys.argv) != 2:
        print("用法：python scene_detect.py 当前截图路径")
        return

    current_path = Path(sys.argv[1])
    try:
        current_image = read_image(current_path)
        current_histogram = make_histogram(current_image)
    except FileNotFoundError as error:
        print(error)
        return

    print(f"待识别图片：{current_path}")
    scores = []
    for scene_name, sample_paths in SCENES.items():
        try:
            sample_scores = []
            for sample_path in sample_paths:
                sample_histogram = make_histogram(read_image(sample_path))
                sample_scores.append(
                    cv2.compareHist(
                        current_histogram, sample_histogram, cv2.HISTCMP_CORREL
                    )
                )
        except FileNotFoundError as error:
            print(error)
            return

        score = max(sample_scores)
        scores.append((score, scene_name))
        print(f"与“{scene_name}”样本组的最高相似度：{score:.3f}")

    best_score, best_scene = max(scores)
    scores.sort(reverse=True)
    second_score = scores[1][0] if len(scores) > 1 else -1.0
    score_gap = best_score - second_score

    # 相似度太低，或第一名和第二名太接近时，不强行猜测场景。
    if best_score < 0.65 or score_gap < 0.10:
        print(f"暂时无法确定场景（最高相似度：{best_score:.3f}，分差：{score_gap:.3f}）")
    else:
        print(f"识别场景：{best_scene}（相似度：{best_score:.3f}，分差：{score_gap:.3f}）")
    print("提示：当前使用颜色分布做初步识别，结果适合学习和观察，不代表百分百准确。")


if __name__ == "__main__":
    main()
