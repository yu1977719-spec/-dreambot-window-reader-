"""Windows 顶层窗口枚举与可见屏幕区域截取。"""

from dataclasses import dataclass
import ctypes
import os

from PIL import ImageGrab
import win32api
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


def get_foreground_window():
    """取得最前面的可见外部应用窗口；必要时按顶层窗口顺序回退。"""

    def to_window_info(hwnd):
        if not hwnd or not win32gui.IsWindow(hwnd):
            return None
        root_hwnd = win32gui.GetAncestor(hwnd, 2)  # GA_ROOT
        if root_hwnd:
            hwnd = root_hwnd
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return None
        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return None
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        if right <= left or bottom <= top:
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid == os.getpid():
            return None
        return WindowInfo(hwnd, pid, title, (left, top, right, bottom))

    foreground = to_window_info(win32gui.GetForegroundWindow())
    if foreground is not None:
        return foreground

    candidates = []

    def collect_first_external(hwnd, _):
        if not candidates:
            target = to_window_info(hwnd)
            if target is not None:
                candidates.append(target)
        return True

    win32gui.EnumWindows(collect_first_external, None)
    return candidates[0] if candidates else None


def capture_window(hwnd):
    """截取窗口位于显示器范围内的可见部分；窗口须未最小化。"""
    if not win32gui.IsWindow(hwnd):
        raise ValueError("窗口已经关闭，请重新扫描窗口列表。")
    if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
        raise ValueError("窗口不可见或已最小化，请先恢复窗口。")

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    virtual_left = win32api.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    virtual_top = win32api.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
    virtual_width = win32api.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
    virtual_height = win32api.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
    virtual_right = virtual_left + virtual_width
    virtual_bottom = virtual_top + virtual_height

    if right <= left or bottom <= top:
        raise ValueError("窗口尺寸无效。")
    capture_left = max(left, virtual_left)
    capture_top = max(top, virtual_top)
    capture_right = min(right, virtual_right)
    capture_bottom = min(bottom, virtual_bottom)
    if capture_right <= capture_left or capture_bottom <= capture_top:
        raise ValueError("窗口完全位于显示器范围外，请移回屏幕内再试。")

    return ImageGrab.grab(
        bbox=(capture_left, capture_top, capture_right, capture_bottom),
        all_screens=True,
    )


def capture_desktop():
    """截取整个虚拟桌面，包括扩展桌面上的所有显示器。"""
    return ImageGrab.grab(all_screens=True)
