---
name: dreambot-window-reader
description: Develop and operate the DreamBot Windows window recognition project for read-only multi-window capture, local scene matching, OCR, and optional DeepSeek explanations. Do not use it to control games or automate mouse and keyboard input.
---

# DreamBot Window Reader

Use this skill when working on the DreamBot Windows project or when the user asks to inspect matching application windows, recognize local screen states, or explain OCR results through the configured DeepSeek API.

## Project behavior

- In the GUI, capture only the current foreground top-level window by default; manual title filtering and full-desktop capture remain available. Apply game scene matching only to Dream of Westward Journey windows, and use generic local OCR for other applications.
- Capture the visible part of each window's desktop region. A window must be visible and not minimized; portions beyond the virtual desktop are clipped. Overlapping windows can obscure the captured pixels.
- Perform scene matching and OCR locally. The default DeepSeek paths send scene names and OCR text only. Upload screenshots only when the user explicitly chooses the `--vision` mode for that request.
- This project is read-only. Never add game clicks, keystrokes, input injection, or gameplay control.
- Keep API keys in the user's environment or registry-backed environment settings. Never put keys in source files, config files, logs, screenshots, examples, or commits.
- Treat screenshots, recordings, OCR logs, task lists, and local scene samples as user data. Do not add them to a Git commit.

## Working with the project

- Use `window_monitor.py` for local multi-window scans and state-change monitoring.
- Use `window_app.py` for the Simplified Chinese graphical interface when the user prefers buttons over command-line commands.
- The GUI can export raw OCR records to UTF-8 CSV for WPS. This is not field mapping or direct workbook editing; confirm the user's template before adding that layer.
- Use `window_monitor.py --desktop` when the user asks to inspect the entire virtual desktop; it performs local OCR only and does not send the screen to the cloud.
- Use `ai_analyze.py --watch` for multi-window monitoring with text-only DeepSeek explanations. `--watch --vision` uploads a screenshot for the changed window and should only be used when explicitly requested.
- Scene recognition requires a local `scene_samples.json` and user-provided reference images. If none exists, leave scene recognition unconfigured and explain how to add local examples; OCR can still run.
- OCR regions can be configured per window title in the local `window_profiles.json`, using normalized left/top/right/bottom coordinates; keep that personal file out of commits.
- Keep the DeepSeek provider configurable through the existing `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL` settings.
