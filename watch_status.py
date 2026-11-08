from window_capture import capture_window as capture_visible_window, find_windows


WINDOW_TITLE_KEYWORD = "梦幻西游"
CHECK_INTERVAL_SECONDS = 3
CONFIRM_READINGS = 2


def find_game_window():
    """保留旧调用接口；返回所有标题匹配的窗口，不只取第一个。"""
    return [(item.hwnd, item.title) for item in find_windows(WINDOW_TITLE_KEYWORD)]


def capture_window(hwnd):
    return capture_visible_window(hwnd)


def main():
    from window_monitor import main as monitor_main

    return monitor_main([
        "--title", WINDOW_TITLE_KEYWORD,
        "--watch",
        "--interval", str(CHECK_INTERVAL_SECONDS),
        "--confirm-readings", str(CONFIRM_READINGS),
    ])


if __name__ == "__main__":
    main()
