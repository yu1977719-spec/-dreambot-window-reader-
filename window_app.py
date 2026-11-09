"""简体中文窗口识别界面；只采集并显示 OCR，不执行点击或键盘操作。"""

import csv
import os
import queue
import re
import threading
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk

from window_capture import find_windows, get_foreground_window
from window_monitor import _read_window, _read_desktop
from window_profiles import load_profiles


class WindowReaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("窗口识别与文字提取")
        self.root.geometry("980x700")
        self.root.minsize(760, 500)

        self.mode = tk.StringVar(value="当前最前窗口")
        self.title_keyword = tk.StringVar(
            value=os.environ.get("DREAMBOT_WINDOW_TITLE", "")
        )
        self.status = tk.StringVar(value="就绪")
        self.results = queue.Queue()
        self.records = []
        self.last_scan_display = ""
        self.previous_scan_display = ""
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
        selector_row = ttk.Frame(controls)
        selector_row.pack(fill="x")

        ttk.Label(selector_row, text="识别目标").pack(side="left")
        self.mode_box = ttk.Combobox(
            selector_row,
            textvariable=self.mode,
            values=("当前最前窗口", "全桌面", "按窗口标题"),
            state="readonly",
            width=20,
        )
        self.mode_box.pack(side="left", padx=(8, 16))
        self.mode_box.bind("<<ComboboxSelected>>", self._update_title_state)

        self.title_controls = ttk.Frame(selector_row)
        self.title_label = ttk.Label(self.title_controls, text="窗口标题包含")
        self.title_label.pack(side="left")
        self.title_entry = ttk.Entry(
            self.title_controls, textvariable=self.title_keyword, width=28
        )
        self.title_entry.pack(side="left", padx=(8, 0))

        actions_row = ttk.Frame(controls)
        actions_row.pack(fill="x", pady=(8, 0))
        self.scan_button = ttk.Button(actions_row, text="识别一次", command=self.start_scan)
        self.scan_button.pack(side="left")
        self.history_button = ttk.Button(
            actions_row,
            text="查看上一次识别记录",
            command=self.show_last_scan,
            state="disabled",
        )
        self.history_button.pack(side="left", padx=(8, 0))
        self.export_button = ttk.Button(
            actions_row,
            text="导出表格文件",
            command=self.export_csv,
            state="disabled",
        )
        self.export_button.pack(side="left", padx=(8, 0))
        ttk.Button(actions_row, text="清空结果", command=self.clear_results).pack(
            side="left", padx=(8, 0)
        )

        ttk.Label(
            frame,
            text="默认只识别当前最前面的一个窗口：把目标程序放在前面，再点“识别一次”，本工具会暂时隐藏并读取刚才的窗口。微信只读右侧聊天框，不识别左侧联系人列表，也不保留聊天时间点。导出为 .csv 表格，可用 WPS 或 Excel 打开；微信表格只保留会话、发送者、内容和状态。OCR 原文请核对。",
            wraplength=920,
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
        self.output.tag_configure(
            "scan_header",
            font=("Microsoft YaHei UI", 11, "bold"),
            foreground="#185a83",
            spacing1=6,
            spacing3=4,
        )
        self.output.tag_configure(
            "field_label",
            font=("Microsoft YaHei UI", 10, "bold"),
            foreground="#333333",
            spacing1=3,
        )
        scrollbar = ttk.Scrollbar(output_frame, orient="vertical", command=self.output.yview)
        self.output.configure(yscrollcommand=scrollbar.set)
        self.output.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ttk.Separator(frame).pack(fill="x", pady=(10, 6))
        ttk.Label(frame, textvariable=self.status, wraplength=920).pack(anchor="w")
        self._update_title_state()

    def _update_title_state(self, _event=None):
        if self.mode.get() == "按窗口标题":
            if not self.title_controls.winfo_manager():
                self.title_controls.pack(side="left")
            self.title_entry.configure(state="normal")
        else:
            self.title_controls.pack_forget()
            self.title_entry.configure(state="disabled")

    def clear_results(self):
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")
        self.records.clear()
        self.last_scan_display = ""
        self.previous_scan_display = ""
        self.history_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        self.status.set("结果已清空。")

    def show_last_scan(self):
        if not self.last_scan_display:
            return

        window = tk.Toplevel(self.root)
        window.title("上一次识别记录")
        window.geometry("760x560")
        window.minsize(520, 360)
        ttk.Label(
            window,
            text="可对照上一次和本次识别结果；记录只保存在当前程序会话中。",
            padding=(12, 10),
        ).pack(anchor="w")

        body = ttk.Frame(window, padding=(12, 0, 12, 12))
        body.pack(fill="both", expand=True)
        text = tk.Text(body, wrap="word", font=("Microsoft YaHei UI", 10), padx=10, pady=10)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        if self.previous_scan_display:
            content = (
                "上一次识别记录\n"
                f"{self.previous_scan_display}\n\n"
                "本次识别记录\n"
                f"{self.last_scan_display}"
            )
        else:
            content = f"目前只有这一条识别记录：\n{self.last_scan_display}"
        text.insert("1.0", content)
        text.configure(state="disabled")

    def export_csv(self):
        if not self.records:
            return

        default_name = f"识别记录_{datetime.now():%Y%m%d_%H%M%S}.csv"
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出识别记录",
            initialfile=default_name,
            defaultextension=".csv",
            filetypes=(("CSV 文件", "*.csv"),),
        )
        if not path:
            return

        columns = ("会话", "发送者", "消息内容", "消息状态")
        rows = [row for record in self.records for row in self._export_rows(record)]
        try:
            with Path(path).open("w", encoding="utf-8-sig", newline="") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=columns)
                writer.writeheader()
                writer.writerows(
                    {
                        column: self._safe_csv_value(record.get(column, ""))
                        for column in columns
                    }
                    for record in rows
                )
        except OSError as error:
            self.status.set(f"导出失败：{error}")
            return

        self.status.set(f"已导出 {len(rows)} 条内容：{path}")
        self._append_result(
            "表格导出完成：已省略进程号、窗口句柄和 OCR 区域等调试信息；撤回通知会标记为“撤回提示”。"
        )

    @staticmethod
    def _export_rows(record):
        title = str(record.get("窗口标题", "") or "")
        ocr_text = str(record.get("OCR原文", "") or "")
        is_wechat = "微信" in title
        if not is_wechat:
            if not ocr_text.strip():
                return []
            return [
                {
                    "会话": title or "当前窗口",
                    "发送者": "",
                    "消息内容": ocr_text.strip(),
                    "消息状态": "",
                }
            ]

        session = title
        rows = []
        for raw_line in ocr_text.splitlines():
            line = raw_line.strip().lstrip("• ").strip()
            if not line:
                continue
            if line.startswith("当前会话："):
                session = line.split("：", 1)[1].strip() or session
                continue
            if re.fullmatch(r"(?:(?:今天|昨天|天)\s*)?\d{1,2}:\d{2}", line):
                continue
            sender = ""
            content = line
            if "：" in line and "撤回" not in line:
                possible_sender, possible_content = line.split("：", 1)
                if 0 < len(possible_sender) <= 12 and possible_content.strip():
                    sender = possible_sender.strip()
                    content = possible_content.strip()
            status = "撤回提示" if "撤回" in line else ""
            rows.append(
                {
                    "会话": session,
                    "发送者": sender,
                    "消息内容": content,
                    "消息状态": status,
                }
            )
        return rows

    @staticmethod
    def _safe_csv_value(value):
        text = "" if value is None else str(value)
        if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
            return "'" + text
        return text

    def _append_result(self, text):
        self.output.configure(state="normal")
        lines = text.rstrip().splitlines()
        for index, line in enumerate(lines):
            if index == 0:
                tag = "scan_header"
            elif line.startswith(
                (
                    "识别对象：",
                    "游戏场景：",
                    "界面状态：",
                    "识别文字：",
                    "聊天范围：",
                    "聊天内容：",
                    "• 撤回提示：",
                )
            ):
                tag = "field_label"
            else:
                tag = None
            if tag is None:
                self.output.insert("end", line + "\n")
            else:
                self.output.insert("end", line + "\n", tag)
        self.output.insert("end", "\n")
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
            if mode == "全桌面":
                result, records = self._scan_desktop()
            elif mode == "当前最前窗口":
                target = get_foreground_window()
                if target is None:
                    raise ValueError(
                        "没有检测到前台应用窗口。请把要识别的程序放到最前面，再点击“识别一次”。"
                    )
                result, records = self._scan_targets([target], mode)
            else:
                result, records = self._scan_windows(title_keyword, mode)
            self.results.put((True, result, records))
        except Exception as error:
            self.results.put((False, f"识别失败：{error}", []))

    @staticmethod
    def _scan_desktop():
        width, height, recognized_text, _ = _read_desktop()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        display = (
            f"[{now}] 全桌面识别完成\n"
            f"画面尺寸：{width} × {height}\n"
            "处理方式：仅在本机识别，未上传截图\n\n"
            f"识别文字：\n{recognized_text or '（没有识别到文字）'}"
        )
        record = {
            "识别时间": now,
            "识别方式": "全桌面",
            "窗口标题": "全桌面",
            "进程号": "",
            "窗口句柄": "",
            "场景": "",
            "相似度": "",
            "界面状态": "",
            "OCR区域": "整个桌面",
            "OCR原文": recognized_text,
        }
        return display, [record]

    @classmethod
    def _scan_windows(cls, title_keyword, recognition_mode="按窗口标题"):
        targets = find_windows(title_keyword)
        if not targets:
            raise ValueError(
                f"没有找到标题包含“{title_keyword}”的可见窗口。"
                "请确认窗口已打开且没有最小化。"
            )
        return cls._scan_targets(targets, recognition_mode)

    @staticmethod
    def _scan_targets(targets, recognition_mode):
        profiles = load_profiles()
        scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if recognition_mode == "当前最前窗口":
            summary = f"识别当前最前窗口（{targets[0].title}）"
        else:
            summary = f"找到 {len(targets)} 个匹配窗口"
        sections = [f"{summary}；识别时间：{scan_time}\n"]
        records = []
        for target in targets:
            try:
                is_game_window = "梦幻西游" in target.title
                is_wechat_window = "微信" in target.title
                _, scene, score, gap, text, status, _, region = _read_window(
                    target, profiles, detect_scene=is_game_window
                )
                region_text = "、".join(f"{value:.2f}" for value in region)
                result_lines = [f"识别对象：{target.title}"]
                if is_wechat_window:
                    result_lines.extend(("聊天范围：右侧聊天面板（已排除左侧联系人列表）", "聊天内容："))
                    display_text = "\n".join(
                        f"• 撤回提示：{line}" if "撤回" in line else f"• {line}"
                        for line in text.splitlines()
                    )
                else:
                    display_text = text
                if is_game_window:
                    result_lines.extend(
                        (
                            f"游戏场景：{scene}（相似度 {score:.3f}）",
                            f"界面状态：{status[1]}",
                        )
                    )
                elif not is_wechat_window and text:
                    display_text = "\n".join(f"• {line}" for line in text.splitlines())
                if not is_wechat_window:
                    result_lines.append("识别文字：")
                result_lines.append(display_text or "（没有识别到文字）")
                sections.append("\n".join(result_lines))
                records.append(
                    {
                        "识别时间": scan_time,
                        "识别方式": recognition_mode,
                        "窗口标题": target.title,
                        "进程号": target.pid,
                        "窗口句柄": target.hwnd,
                        "场景": scene if is_game_window else "未进行场景分类",
                        "相似度": f"{score:.3f}" if is_game_window else "",
                        "界面状态": status[1] if is_game_window else "",
                        "OCR区域": region_text,
                        "OCR原文": text,
                    }
                )
            except Exception as error:
                sections.append(f"窗口：{target.title}\n读取失败：{error}")
        return "\n\n".join(sections), records

    def _poll_result(self):
        try:
            success, result, records = self.results.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_result)
            return

        self.root.deiconify()
        self._append_result(result)
        if records:
            if self.last_scan_display:
                self.previous_scan_display = self.last_scan_display
            self.last_scan_display = result
            self.history_button.configure(state="normal")
            self.records.extend(records)
            self.export_button.configure(state="normal")
            self.status.set(f"本次识别 {len(records)} 条；累计可导出 {len(self.records)} 条记录。")
        else:
            self.status.set("识别完成，但没有可导出的记录。" if success else "识别遇到问题，请查看结果区。")
        self.scan_button.configure(state="normal")
        self.busy = False


def main():
    root = tk.Tk()
    WindowReaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
