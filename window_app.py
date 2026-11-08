"""简体中文窗口识别界面；只采集并显示 OCR，不执行点击或键盘操作。"""

import os
import queue
import threading
from datetime import datetime

import tkinter as tk
from tkinter import ttk

from window_capture import find_windows
from window_monitor import _read_window, _read_desktop
from window_profiles import load_profiles


class WindowReaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("窗口识别与文字提取")
        self.root.geometry("900x640")
        self.root.minsize(680, 480)

        self.mode = tk.StringVar(value="全桌面")
        self.title_keyword = tk.StringVar(
            value=os.environ.get("DREAMBOT_WINDOW_TITLE", "梦幻西游")
        )
        self.status = tk.StringVar(value="就绪")
        self.results = queue.Queue()
        self.busy = False

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="窗口识别与文字提取", font=("Microsoft YaHei UI", 18, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            frame,
            text="本地截图与 OCR · 不点击、不按键 · 当前版本不会写入 WPS",
        ).pack(anchor="w", pady=(4, 14))

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(0, 10))

        ttk.Label(controls, text="识别范围").pack(side="left")
        self.mode_box = ttk.Combobox(
            controls,
            textvariable=self.mode,
            values=("全桌面", "按窗口标题"),
            state="readonly",
            width=16,
        )
        self.mode_box.pack(side="left", padx=(8, 16))
        self.mode_box.bind("<<ComboboxSelected>>", self._update_title_state)

        ttk.Label(controls, text="窗口标题包含").pack(side="left")
        self.title_entry = ttk.Entry(controls, textvariable=self.title_keyword, width=24)
        self.title_entry.pack(side="left", padx=(8, 16))

        self.scan_button = ttk.Button(controls, text="识别一次", command=self.start_scan)
        self.scan_button.pack(side="left")
        ttk.Button(controls, text="清空结果", command=self.clear_results).pack(
            side="left", padx=(8, 0)
        )

        ttk.Label(
            frame,
            text="全桌面会读取屏幕上所有可见文字；识别期间界面会暂时隐藏。",
            wraplength=850,
        ).pack(anchor="w", pady=(0, 8))

        output_frame = ttk.Frame(frame)
        output_frame.pack(fill="both", expand=True)
        self.output = tk.Text(
            output_frame,
            wrap="word",
            font=("Microsoft YaHei UI", 10),
            padx=10,
            pady=10,
            state="disabled",
        )
        scrollbar = ttk.Scrollbar(output_frame, orient="vertical", command=self.output.yview)
        self.output.configure(yscrollcommand=scrollbar.set)
        self.output.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ttk.Separator(frame).pack(fill="x", pady=(10, 6))
        ttk.Label(frame, textvariable=self.status).pack(anchor="w")
        self._update_title_state()

    def _update_title_state(self, _event=None):
        state = "normal" if self.mode.get() == "按窗口标题" else "disabled"
        self.title_entry.configure(state=state)

    def clear_results(self):
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def _append_result(self, text):
        self.output.configure(state="normal")
        self.output.insert("end", text.rstrip() + "\n\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def start_scan(self):
        if self.busy:
            return
        if self.mode.get() == "按窗口标题" and not self.title_keyword.get().strip():
            self.status.set("请先填写窗口标题关键词。")
            return

        self.busy = True
        self.scan_button.configure(state="disabled")
        self.status.set("正在准备识别……")
        self.root.withdraw()
        self.root.after(300, self._launch_worker)

    def _launch_worker(self):
        mode = self.mode.get()
        title_keyword = self.title_keyword.get().strip()
        threading.Thread(
            target=self._scan_worker,
            args=(mode, title_keyword),
            daemon=True,
        ).start()
        self.root.after(100, self._poll_result)

    def _scan_worker(self, mode, title_keyword):
        try:
            result = self._scan_desktop() if mode == "全桌面" else self._scan_windows(title_keyword)
            self.results.put((True, result))
        except Exception as error:
            self.results.put((False, f"识别失败：{error}"))

    @staticmethod
    def _scan_desktop():
        width, height, recognized_text, _ = _read_desktop()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return (
            f"[{now}] 全桌面识别完成\n"
            f"画面尺寸：{width} × {height}\n"
            "处理方式：仅在本机识别，未上传截图\n\n"
            f"识别文字：\n{recognized_text or '（没有识别到文字）'}"
        )

    @staticmethod
    def _scan_windows(title_keyword):
        targets = find_windows(title_keyword)
        if not targets:
            raise ValueError(
                f"没有找到标题包含“{title_keyword}”的可见窗口。"
                "请确认窗口已打开且没有最小化。"
            )

        profiles = load_profiles()
        sections = [f"找到 {len(targets)} 个匹配窗口；识别时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"]
        for target in targets:
            try:
                _, scene, score, gap, text, status, _, region = _read_window(target, profiles)
                region_text = "、".join(f"{value:.2f}" for value in region)
                sections.append(
                    f"窗口：{target.title}\n"
                    f"进程号：{target.pid}；窗口句柄：{target.hwnd}\n"
                    f"场景：{scene}（相似度 {score:.3f}，分差 {gap:.3f}）\n"
                    f"界面状态：{status[1]}\n"
                    f"OCR 区域：{region_text}\n"
                    f"识别文字：\n{text or '（没有识别到文字）'}"
                )
            except Exception as error:
                sections.append(f"窗口：{target.title}\n读取失败：{error}")
        return "\n\n".join(sections)

    def _poll_result(self):
        try:
            success, result = self.results.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_result)
            return

        self.root.deiconify()
        self._append_result(result)
        self.status.set("识别完成。" if success else "识别遇到问题，请查看结果区。")
        self.scan_button.configure(state="normal")
        self.busy = False


def main():
    root = tk.Tk()
    WindowReaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
