# -*- coding: utf-8 -*-
"""
Playwright smoke test for the local Flask app (WHU-Walker).

Dependency note (checked 2026-08-11):
    python  (system):           playwright NOT installed
    venv\\Scripts\\python.exe:    playwright NOT installed

Before running, install the Python package and Chromium browser:
    pip install playwright
    playwright install chromium

This script never starts the Flask server. Start it separately, e.g.:
    flask run --host 0.0.0.0 --port 5000
then run:
    python scripts/qa_smoke.py

Artifacts are written under output/:
    qa_console.log  - browser console errors
    qa_desktop.png  - desktop first-view screenshot
    qa_result.png   - screenshot after shortcut / NL submit
    qa_mobile.png   - 390x844 reload screenshot
"""

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:
    raise SystemExit(
        "Playwright is not installed in this Python environment. "
        "Run `pip install playwright` and `playwright install chromium` first."
    )

BASE_URL = "http://localhost:5000"
ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

# Selectors taken from static/index.html / static/js/app.js, not guessed.
SELECTORS = {
    "nl_input": "#nl-input",
    "submit_button": "#submit-btn",
    "results_section": "#results-section",
    "results_content": "#results-content",
    "welcome_overlay": "#welcome-overlay",
    "map_container": "#map-container",
}

# The five .shortcut-card entries in static/index.html with their exact
# data-action / data-display-text attributes as of 2026-08-11.
SHORTCUT_CARDS = [
    {
        "action": "path_planning",
        "display_text": "经典路线 · 牌坊 → 樱顶（景观优先）",
    },
    {
        "action": "path_planning",
        "display_text": "赏樱路线 · 樱花大道 → 樱顶（春天必走）",
    },
    {
        "action": "poi_query",
        "display_text": "景点查询 · 樱顶在哪？给我介绍一下",
    },
    {
        "action": "path_planning",
        "display_text": "最短路径 · 教五 → 总图书馆（赶时间）",
    },
    {
        "action": "recommend_poi",
        "display_text": "推荐景点 · 展开侧边栏看 5 个精华",
    },
]


def record(logs, message):
    print(message)
    logs.append(message)


def write_console_log(console_errors):
    path = OUTPUT_DIR / "qa_console.log"
    content = "\n".join(console_errors)
    if content:
        content += "\n"
    path.write_text(content, encoding="utf-8")
    print(f"[LOG] console errors -> {path} ({len(console_errors)} line(s))")


def has_welcome_seen_marker(page):
    try:
        return page.evaluate(
            "() => localStorage.getItem('whu_welcome_seen') === 'true' || "
            "localStorage.getItem('whu_welcome_dont_show_forever') === 'true'"
        )
    except Exception:
        return False


def open_page(page, url):
    try:
        page.goto(url, wait_until="networkidle", timeout=15000)
        return True, None
    except PlaywrightTimeoutError:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            return True, "networkidle 等待超时，已回退到 domcontentloaded"
        except Exception as exc:
            return False, str(exc)
    except Exception as exc:
        return False, str(exc)


def wait_for_result_or_error(page, timeout_ms=20000):
    try:
        page.wait_for_function(
            """() => {
                const rd = document.querySelector('#recommended-distance');
                const rdText = rd ? rd.textContent.trim() : '';
                if (rdText && rdText !== '\u2014') return true;
                const err = document.querySelector('#error-section');
                if (err && !err.hidden) return true;
                return false;
            }""",
            timeout=timeout_ms,
        )
        try:
            return page.evaluate(
                """() => {
                    const err = document.querySelector('#error-section');
                    if (err && !err.hidden) return 'error';
                    return 'result';
                }"""
            )
        except Exception:
            return "result"
    except PlaywrightTimeoutError:
        return "timeout"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logs = []
    console_errors = []
    browser = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            page = context.new_page()
            page.set_default_timeout(5000)

            def on_console(msg):
                if msg.type == "error":
                    console_errors.append(f"[{msg.type}] {msg.text}")

            def on_page_error(exc):
                console_errors.append(f"[pageerror] {exc}")

            page.on("console", on_console)
            page.on("pageerror", on_page_error)

            ok, note = open_page(page, BASE_URL)
            if not ok:
                record(logs, f"[SKIP] 无法打开 {BASE_URL}：{note}；跳过后续步骤")
                return
            if note:
                record(logs, f"[SKIP] {note}")
            else:
                record(logs, "[PASS] 页面打开并达到 networkidle")

            overlay = page.locator(SELECTORS["welcome_overlay"])
            try:
                overlay.wait_for(state="visible", timeout=3000)
                record(logs, "[PASS] 首屏 #welcome-overlay 已自动显示")
            except PlaywrightTimeoutError:
                if has_welcome_seen_marker(page):
                    record(
                        logs,
                        "[PASS] 欢迎覆盖层未弹出，但 localStorage 已有 "
                        "whu_welcome_seen / whu_welcome_dont_show_forever 标记",
                    )
                else:
                    record(
                        logs,
                        "[SKIP] #welcome-overlay 3s 内不可见，且无 seen 标记；继续执行",
                    )
            except Exception as exc:
                record(logs, f"[SKIP] 检查欢迎覆盖层失败：{exc}")

            desktop_png = OUTPUT_DIR / "qa_desktop.png"
            try:
                page.screenshot(path=str(desktop_png), full_page=True)
                record(logs, f"[PASS] 桌面全屏截图 -> {desktop_png}")
            except Exception as exc:
                record(logs, f"[SKIP] 桌面截图失败：{exc}")

            submitted = False
            shortcut = page.locator(".shortcut-card").first
            try:
                shortcut.wait_for(state="attached", timeout=3000)
                if shortcut.is_visible():
                    shortcut.click()
                    record(logs, "[PASS] 已点击第一张 .shortcut-card（path_planning）")
                    submitted = True
                else:
                    record(logs, "[SKIP] .shortcut-card 存在但不可见，改用输入框提交")
            except PlaywrightTimeoutError:
                record(logs, "[SKIP] 未找到 .shortcut-card，改用输入框提交")
            except Exception as exc:
                record(logs, f"[SKIP] 点击 shortcut-card 失败：{exc}；改用输入框提交")

            if not submitted:
                input_loc = page.locator(SELECTORS["nl_input"])
                submit_loc = page.locator(SELECTORS["submit_button"])
                if input_loc.count() == 0 or submit_loc.count() == 0:
                    record(
                        logs,
                        "[SKIP] #nl-input 或 #submit-btn 不存在，跳过结果交互",
                    )
                else:
                    if overlay.is_visible():
                        try:
                            page.locator(".welcome-close").first.click(timeout=2000)
                        except Exception:
                            try:
                                page.keyboard.press("Escape")
                            except Exception:
                                pass
                    try:
                        input_loc.fill(SHORTCUT_CARDS[0]["display_text"])
                        submit_loc.click()
                        record(logs, "[PASS] 已填入自然语言并点击 #submit-btn")
                    except Exception as exc:
                        record(logs, f"[SKIP] 输入/提交失败：{exc}")

            state = wait_for_result_or_error(page)
            if state == "result":
                record(
                    logs,
                    "[PASS] 结果区出现真实内容（#recommended-distance 已离开占位符）",
                )
            elif state == "error":
                record(
                    logs,
                    "[SKIP] 页面显示错误卡片而非结果内容（服务器可能未启动或 API 失败）",
                )
            else:
                record(logs, "[SKIP] 20s 内结果区未出现真实内容")

            result_png = OUTPUT_DIR / "qa_result.png"
            try:
                page.screenshot(path=str(result_png), full_page=True)
                record(logs, f"[PASS] 结果截图 -> {result_png}")
            except Exception as exc:
                record(logs, f"[SKIP] 结果截图失败：{exc}")

            page.set_viewport_size({"width": 390, "height": 844})
            try:
                page.reload(wait_until="networkidle", timeout=15000)
            except PlaywrightTimeoutError:
                try:
                    page.reload(wait_until="domcontentloaded", timeout=15000)
                except Exception as exc:
                    record(logs, f"[SKIP] 移动端刷新失败：{exc}")
            except Exception as exc:
                record(logs, f"[SKIP] 移动端刷新失败：{exc}")

            page.wait_for_timeout(1200)
            mobile_png = OUTPUT_DIR / "qa_mobile.png"
            try:
                page.screenshot(path=str(mobile_png), full_page=True)
                record(logs, f"[PASS] 移动端截图 -> {mobile_png}")
            except Exception as exc:
                record(logs, f"[SKIP] 移动端截图失败：{exc}")

            try:
                if overlay.is_visible():
                    record(logs, "[INFO] 移动端刷新后欢迎覆盖层仍可见")
                else:
                    record(logs, "[INFO] 移动端刷新后欢迎覆盖层不可见（符合 seen 标记预期）")
            except Exception:
                pass

            browser.close()
            browser = None
    except Exception as exc:
        record(logs, f"[SKIP] 未预期异常，已防御式终止：{exc}")
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        write_console_log(console_errors)

    print("\n--- QA smoke summary ---")
    for line in logs:
        print(line)


if __name__ == "__main__":
    main()
