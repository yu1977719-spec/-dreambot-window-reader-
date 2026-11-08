"""读取本机窗口配置，并为不同应用选择归一化 OCR 区域。"""

import json
import math
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
PROFILE_CONFIG = PROJECT_DIR / "window_profiles.json"
DEFAULT_GAME_REGION = (0.78, 0.20, 0.99, 0.48)
FULL_WINDOW_REGION = (0.0, 0.0, 1.0, 1.0)


def load_profiles(path=PROFILE_CONFIG):
    """读取标题匹配规则；缺少本机配置时返回空列表。"""
    path = Path(path)
    if not path.exists():
        return []

    try:
        with path.open("r", encoding="utf-8") as config_file:
            config = json.load(config_file)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取窗口配置 {path.name}：{error}") from error

    entries = config.get("profiles") if isinstance(config, dict) else None
    if not isinstance(entries, list):
        raise ValueError(f"{path.name} 必须包含 profiles 列表。")

    profiles = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"{path.name} 第 {index} 条配置必须是对象。")

        keyword = entry.get("title_contains")
        region = entry.get("ocr_region")
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError(f"{path.name} 第 {index} 条配置缺少 title_contains。")
        if not isinstance(region, list) or len(region) != 4:
            raise ValueError(f"{path.name} 第 {index} 个 ocr_region 必须是 4 个 0 到 1 之间的数字。")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            or value > 1
            for value in region
        ):
            raise ValueError(f"{path.name} 第 {index} 个 ocr_region 坐标必须在 0 到 1 之间。")

        left, top, right, bottom = (float(value) for value in region)
        if left >= right or top >= bottom:
            raise ValueError(f"{path.name} 第 {index} 个 ocr_region 左上角必须位于右下角之前。")

        profiles.append(
            {
                "title_contains": keyword.strip().casefold(),
                "ocr_region": (left, top, right, bottom),
            }
        )

    return profiles


def resolve_ocr_region(window_title, profiles):
    """按首个匹配标题选择区域；未配置游戏窗口时保留旧版区域。"""
    title = (window_title or "").casefold()
    for profile in profiles:
        if profile["title_contains"] in title:
            return profile["ocr_region"]
    if "梦幻西游" in title:
        return DEFAULT_GAME_REGION
    return FULL_WINDOW_REGION
