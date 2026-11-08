"""Windows 顶层窗口枚举与可见屏幕区域截取。"""

from dataclasses import dataclass
import ctypes
import os

from PIL import ImageGrab
import win32gui
import win32process


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    bounds: tuple[int, int, int, int]


def _enable_dpi_awareness():
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_enable_dpi_awareness()


def find_windows(title_keyword="梦幻西游"):
    """按标题筛选可见、未最小化的窗口，并保留 HWND/PID 供多开区分。"""
    keyword = (title_keyword or "").strip().casefold()
    matches = []

    def collect(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).strip()
        if not title or (keyword and keyword not in title.casefold()):
            return
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        if right <= left or bottom <= top:
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        matches.append(WindowInfo(hwnd, pid, title, (left, top, right, bottom)))

    win32gui.EnumWindows(collect, None)
    return sorted(matches, key=lambda item: (item.title.casefold(), item.pid, item.hwnd))


def capture_window(hwnd):
    """截取窗口所在的桌面区域；窗口须未最小化且完整位于显示器范围内。"""
    if not win32gui.IsWindow(hwnd):
        raise ValueError("窗口已经关闭，请重新扫描窗口列表。")
    if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
        raise ValueError("窗口不可见或已最小化，请先恢复窗口。")

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    virtual_left = win32gui.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    virtual_top = win32gui.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
    virtual_width = win32gui.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
    virtual_height = win32gui.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
    virtual_right = virtual_left + virtual_width
    virtual_bottom = virtual_top + virtual_height

    if right <= left or bottom <= top:
        raise ValueError("窗口尺寸无效。")
    if (
        left < virtual_left
        or top < virtual_top
        or right > virtual_right
        or bottom > virtual_bottom
    ):
        raise ValueError("窗口没有完整显示在桌面上，请移回显示器范围内。")

    return ImageGrab.grab(
        bbox=(left, top, right, bottom),
        all_screens=True,
    )
