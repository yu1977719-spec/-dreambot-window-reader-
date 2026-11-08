"""只读监视多个 Windows 窗口，并在画面状态变化时显示本地 OCR 结果。"""

import argparse
import os
import time
from datetime import datetime

import cv2
import numpy as np

from screen_status import SCENE_CONFIG, SCENES, choose_status, find_scene, read_task_panel_text
from window_capture import capture_desktop, capture_window, find_windows
from window_profiles import FULL_WINDOW_REGION, load_profiles, resolve_ocr_region


def _read_window(target, profiles):
    screenshot = capture_window(target.hwnd)
    image = cv2.cvtColor(np.asarray(screenshot), cv2.COLOR_RGB2BGR)
    scene_name, score, gap = find_scene(image)
    ocr_region = resolve_ocr_region(target.title, profiles)
    recognized_text = read_task_panel_text(
        image,
        region=ocr_region,
    )
    status = choose_status(scene_name, recognized_text)
    signature = (scene_name, status[1], recognized_text.strip()[:300])
    return image, scene_name, score, gap, recognized_text, status, signature, ocr_region


def _print_result(target, scene_name, score, gap, recognized_text, status, ocr_region):
    now = datetime.now().strftime("%H:%M:%S")
    print(
        f"[{now}] 窗口={target.title!r}；HWND={target.hwnd}；PID={target.pid}；"
        f"场景={scene_name}；相似度={score:.3f}；分差={gap:.3f}"
    )
    print(f"  界面状态：{status[1]}")
    print(f"  OCR 区域：{', '.join(f'{value:.2f}' for value in ocr_region)}")
    print(f"  OCR：{recognized_text or '（没有识别到文字）'}")


def _read_desktop():
    screenshot = capture_desktop()
    image = cv2.cvtColor(np.asarray(screenshot), cv2.COLOR_RGB2BGR)
    height, width = image.shape[:2]
    recognized_text = read_task_panel_text(image, region=FULL_WINDOW_REGION)
    signature = recognized_text.strip()[:500]
    return width, height, recognized_text, signature


def _monitor_desktop(args):
    pending_signature = None
    stable_count = 0
    last_reported = None

    if args.watch:
        print("开始只读监视整个桌面；OCR 仅在本机运行；按 Ctrl+C 停止。")

    try:
        while True:
            try:
                width, height, recognized_text, signature = _read_desktop()
            except Exception as error:
                print(f"全桌面读取失败：{error}")
                if not args.watch:
                    return 1
                time.sleep(args.interval)
                continue

            stable_count = stable_count + 1 if signature == pending_signature else 1
            pending_signature = signature
            if not args.watch or (
                stable_count >= args.confirm_readings and signature != last_reported
            ):
                now = datetime.now().strftime("%H:%M:%S")
                print(f"[{now}] 全桌面；尺寸={width}x{height}；OCR 区域=整个桌面")
                print(f"  OCR：{recognized_text or '（没有识别到文字）'}")
                last_reported = signature

            if not args.watch:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("全桌面监视已停止。")
        return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="只读扫描多个窗口或整个桌面；不点击、不按键、不控制游戏。"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--title",
        help="窗口标题包含的文字；默认读取 DREAMBOT_WINDOW_TITLE，未设置时为“梦幻西游”。",
    )
    group.add_argument(
        "--all-windows",
        action="store_true",
        help="扫描全部可见窗口。请仅在确认屏幕内容适合读取时使用。",
    )
    group.add_argument(
        "--desktop",
        action="store_true",
        help="截取整个虚拟桌面并在本机 OCR，不按窗口标题筛选。",
    )
    parser.add_argument("--watch", action="store_true", help="持续监视，只在状态变化后输出。")
    parser.add_argument("--interval", type=float, default=3.0, help="监视间隔秒数，默认 3 秒。")
    parser.add_argument("--confirm-readings", type=int, default=2, help="输出前需连续稳定的次数。")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.interval <= 0:
        print("监视间隔必须大于 0 秒。")
        return 2
    if args.confirm_readings < 1:
        print("连续确认次数必须至少为 1。")
        return 2

    title_keyword = None if args.all_windows or args.desktop else (
        args.title if args.title is not None else os.environ.get("DREAMBOT_WINDOW_TITLE", "梦幻西游")
    )
    if args.desktop:
        return _monitor_desktop(args)

    title_description = "全部窗口" if args.all_windows else f"标题包含“{title_keyword}”"

    try:
        profiles = load_profiles()
    except ValueError as error:
        print(f"窗口配置有误：{error}")
        print("请修正 window_profiles.json，或先将它移走以使用默认 OCR 区域。")
        return 2

    if not SCENES:
        print(
            f"未配置场景样本（{SCENE_CONFIG.name} 不存在或为空）；"
            "本次仍会执行 OCR。复制 scene_samples.example.json 为 scene_samples.json，"
            "再添加本机参考截图即可启用场景识别。"
        )

    if args.watch:
        print(f"开始只读监视{title_description}；按 Ctrl+C 停止。")

    pending = {}
    last_reported = {}
    try:
        while True:
            targets = find_windows(title_keyword)
            if not targets:
                print(f"没有找到{title_description}的可见窗口。")
                if not args.watch:
                    return 1
                time.sleep(args.interval)
                continue

            live_keys = {(target.hwnd, target.pid) for target in targets}
            for key in list(pending):
                if key not in live_keys:
                    pending.pop(key, None)
                    last_reported.pop(key, None)

            for target in targets:
                key = (target.hwnd, target.pid)
                try:
                    (
                        _, scene_name, score, gap, recognized_text, status,
                        signature, ocr_region,
                    ) = _read_window(target, profiles)
                except Exception as error:
                    print(f"读取窗口 {target.title!r}（HWND={target.hwnd}）失败：{error}")
                    continue

                previous_signature, count = pending.get(key, (None, 0))
                count = count + 1 if signature == previous_signature else 1
                pending[key] = (signature, count)

                if not args.watch:
                    _print_result(
                        target, scene_name, score, gap, recognized_text, status, ocr_region
                    )
                    continue

                if count >= args.confirm_readings and signature != last_reported.get(key):
                    _print_result(
                        target, scene_name, score, gap, recognized_text, status, ocr_region
                    )
                    last_reported[key] = signature

            if not args.watch:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("监视已停止。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
