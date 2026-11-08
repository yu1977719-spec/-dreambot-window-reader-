from datetime import datetime
from pathlib import Path

from window_capture import capture_window, find_windows


PROJECT_DIR = Path(__file__).resolve().parent
SCREENSHOT_DIR = PROJECT_DIR / "screenshots"
WINDOW_TITLE_KEYWORD = "梦幻西游"


def find_game_window():
    """查找所有标题匹配的窗口。"""
    return [(item.hwnd, item.title) for item in find_windows(WINDOW_TITLE_KEYWORD)]


def main():
    windows = find_game_window()
    if not windows:
        print(f"没有找到标题包含“{WINDOW_TITLE_KEYWORD}”的可见窗口。")
        return

    hwnd, title = windows[0]

    SCREENSHOT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = SCREENSHOT_DIR / f"game_{timestamp}.png"

    try:
        screenshot = capture_window(hwnd)
        screenshot.save(output_path)
    except ValueError as error:
        print(f"无法截取窗口：{error}")
        return

    print(f"找到窗口：{title}")
    print(f"匹配到的窗口数：{len(windows)}；本次截取第一个匹配窗口。")
    print(f"截图已保存：{output_path}")
    print(f"截图尺寸：{screenshot.width} × {screenshot.height}")


if __name__ == "__main__":
    main()
