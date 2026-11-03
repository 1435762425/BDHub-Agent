#!/usr/bin/env python3
"""Kalodata login helper for macOS and Windows.

The launcher opens a dedicated Chrome profile. Existing cookies from
``latest_cookie.txt`` are imported when available, while a normal manual login
also works on first use. Cookies applicable to Kalodata are exported every few
seconds so the scraper can reuse the current session.
"""

from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from playwright.sync_api import BrowserContext, Error as PlaywrightError, sync_playwright


APP_DIR = Path(__file__).resolve().parent
COOKIE_PATH = APP_DIR / "latest_cookie.txt"
PROFILE_DIR = APP_DIR / ".chrome-profile"
EXTENSION_DIR = Path(
    os.getenv(
        "KALODATA_EXTENSION_DIR",
        "/Users/bjn00003/Desktop/Chrome/kalodata/kalodata",
    )
).expanduser().resolve()
EXTENSION_RUNTIME_PATH = APP_DIR / "extension_runtime.json"
TARGET_URL = os.getenv("KALODATA_LOGIN_URL", "https://www.kalodata.com/explore").strip()
COOKIE_SCOPE_URL = "https://www.kalodata.com/product/detail"
SAVE_INTERVAL_SECONDS = 3

_stop_requested = False


def _request_stop(_signum: int, _frame: Any) -> None:
    global _stop_requested
    _stop_requested = True


def _clean_cookie_string(value: str) -> str:
    cookie = value.strip()
    if "\r" in cookie or "\n" in cookie:
        return ""
    return cookie


def read_saved_cookie() -> str:
    try:
        cookie = _clean_cookie_string(COOKIE_PATH.read_text(encoding="utf-8-sig"))
    except OSError:
        return ""
    if cookie:
        print(f"已读取本地 Cookie（{len(cookie)} 个字符）。")
    return cookie


def parse_cookie_header(cookie_string: str) -> list[dict[str, Any]]:
    """Convert a Cookie request header into Playwright cookie objects."""
    parsed: dict[str, str] = {}
    for item in cookie_string.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        if name:
            parsed[name] = value.strip()

    return [
        {
            "name": name,
            "value": value,
            "domain": ".kalodata.com",
            "path": "/",
            "secure": True,
        }
        for name, value in parsed.items()
    ]


def export_current_cookie(context: BrowserContext) -> bool:
    """Atomically export cookies that would be sent to a Kalodata API URL."""
    cookies = [
        cookie
        for cookie in context.cookies([COOKIE_SCOPE_URL])
        if str(cookie.get("domain", "")).lstrip(".").endswith("kalodata.com")
    ]
    if not cookies:
        return False

    cookie_string = "; ".join(
        f"{cookie['name']}={cookie['value']}"
        for cookie in cookies
        if cookie.get("name") and cookie.get("value") is not None
    )
    if not cookie_string:
        return False

    try:
        if COOKIE_PATH.exists() and COOKIE_PATH.read_text(encoding="utf-8-sig").strip() == cookie_string:
            return True
    except OSError:
        pass

    temporary_path = COOKIE_PATH.with_name(f".{COOKIE_PATH.name}.tmp")
    temporary_path.write_text(cookie_string, encoding="utf-8")
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, COOKIE_PATH)
    print(f"已更新 Cookie：{len(cookies)} 项。")
    return True


def launch_context(playwright: Any) -> BrowserContext:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(PROFILE_DIR, 0o700)
    browser_args = [
        "--disable-blink-features=AutomationControlled",
        "--start-maximized",
    ]
    extension_available = (EXTENSION_DIR / "manifest.json").is_file()
    launch_options: dict[str, Any] = {}
    if extension_available:
        browser_args.extend(
            [
                f"--disable-extensions-except={EXTENSION_DIR}",
                f"--load-extension={EXTENSION_DIR}",
            ]
        )
        launch_options["ignore_default_args"] = ["--disable-extensions"]
    else:
        launch_options["channel"] = "chrome"
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        no_viewport=True,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        args=browser_args,
        **launch_options,
    )


def wait_for_extension_id(context: BrowserContext, timeout_seconds: int = 20) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for worker in context.service_workers:
            if worker.url.startswith("chrome-extension://"):
                return worker.url.split("/", 3)[2]
        try:
            worker = context.wait_for_event("serviceworker", timeout=1_000)
        except PlaywrightError:
            continue
        if worker.url.startswith("chrome-extension://"):
            return worker.url.split("/", 3)[2]
    return ""


def activate_and_login_extension(context: BrowserContext, extension_id: str, activation_code: str) -> None:
    """Activate the unpacked extension without logging or persisting the code here."""
    popup = context.new_page()
    try:
        popup.goto(f"chrome-extension://{extension_id}/popup.html", wait_until="domcontentloaded")
        activate_view = popup.locator("#activate-view")
        login_view = popup.locator("#login-view")

        if activate_view.is_visible():
            popup.locator("#activate-code").fill(activation_code)
            popup.locator("#activate-btn").click()
            login_view.wait_for(state="visible", timeout=30_000)
            print("扩展激活成功。")

        card_input = popup.locator("#card-number")
        card_input.fill(activation_code)
        popup.locator("#fetch-cookie").click()

        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            message = popup.locator("#error-message").inner_text().strip()
            update_visible = popup.locator("#update-modal").get_attribute("class") or ""
            if "登录成功" in message or "show" in update_visible.split():
                print("扩展验证登录成功，代理与 Cookie 已配置。")
                return
            button_enabled = popup.locator("#fetch-cookie").is_enabled()
            if button_enabled and message and "正在验证" not in message:
                raise RuntimeError(f"扩展验证登录失败：{message}")
            popup.wait_for_timeout(500)
        raise RuntimeError("扩展验证登录超时。")
    finally:
        popup.close()


def refresh_extension_login(context: BrowserContext, extension_id: str) -> bool:
    """Refresh extension-provided proxy/Cookie using its already saved card."""
    popup = context.new_page()
    try:
        popup.goto(f"chrome-extension://{extension_id}/popup.html", wait_until="domcontentloaded")
        if not popup.locator("#login-view").is_visible():
            return False
        card_input = popup.locator("#card-number")
        if not card_input.input_value().strip():
            return False
        popup.locator("#fetch-cookie").click()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            message = popup.locator("#error-message").inner_text().strip()
            update_visible = popup.locator("#update-modal").get_attribute("class") or ""
            if "登录成功" in message or "show" in update_visible.split():
                print("扩展验证登录已刷新。")
                return True
            button_enabled = popup.locator("#fetch-cookie").is_enabled()
            if button_enabled and message and "正在验证" not in message:
                raise RuntimeError(f"扩展验证登录失败：{message}")
            popup.wait_for_timeout(500)
        raise RuntimeError("扩展验证登录超时。")
    finally:
        popup.close()


def export_extension_runtime(context: BrowserContext, extension_id: str) -> bool:
    """Export only the extension proxy needed by the local scraper."""
    page = context.new_page()
    try:
        raw_proxy: Any = None
        for worker in context.service_workers:
            if worker.url.startswith(f"chrome-extension://{extension_id}/"):
                try:
                    raw_proxy = worker.evaluate(
                        "() => (typeof state !== 'undefined' && state.currentProxy) ? state.currentProxy : null"
                    )
                except PlaywrightError:
                    raw_proxy = None
                break
        page.goto(f"chrome-extension://{extension_id}/popup.html", wait_until="domcontentloaded")
        if raw_proxy is None:
            values = page.evaluate("async () => await chrome.storage.local.get(['proxy'])")
            raw_proxy = values.get("proxy") if isinstance(values, dict) else None
        if isinstance(raw_proxy, dict):
            host = str(raw_proxy.get("host") or "").strip()
            port = str(raw_proxy.get("port") or "").strip()
            username = str(raw_proxy.get("username") or "").strip()
            password = str(raw_proxy.get("password") or "").strip()
            proxy_url = (
                f"http://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"
                if host and port else ""
            )
        else:
            raw_text = str(raw_proxy or "").strip()
            if raw_text.startswith(("http://", "https://", "socks5://")):
                proxy_url = raw_text
            else:
                parts = raw_text.split("|")
                if len(parts) < 4 or not parts[0].strip() or not parts[1].strip():
                    proxy_url = ""
                else:
                    host, port, username, password = (part.strip() for part in parts[:4])
                    proxy_url = f"http://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"
        if not proxy_url:
            proxy_settings = page.evaluate(
                "async () => await new Promise(resolve => chrome.proxy.settings.get({incognito: false}, resolve))"
            )
            value = proxy_settings.get("value", {}) if isinstance(proxy_settings, dict) else {}
            rules = value.get("rules", {}) if isinstance(value, dict) else {}
            single_proxy = rules.get("singleProxy", {}) if isinstance(rules, dict) else {}
            if isinstance(single_proxy, dict):
                host = str(single_proxy.get("host") or "").strip()
                port = str(single_proxy.get("port") or "").strip()
                scheme = str(single_proxy.get("scheme") or "http").strip()
                if host and port:
                    proxy_url = f"{scheme}://{host}:{port}"
        if not proxy_url:
            proxy_keys = sorted(raw_proxy.keys()) if isinstance(raw_proxy, dict) else []
            print(f"扩展代理格式未识别：{type(raw_proxy).__name__}，字段={proxy_keys}")
            return False
        payload = {
            "proxy_url": proxy_url,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": "kalodata_extension",
        }
        temporary = EXTENSION_RUNTIME_PATH.with_name(f".{EXTENSION_RUNTIME_PATH.name}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, EXTENSION_RUNTIME_PATH)
        os.chmod(EXTENSION_RUNTIME_PATH, 0o600)
        print("扩展代理已同步到本地抓取运行时。")
        return True
    finally:
        page.close()


def main() -> int:
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    print("正在启动 Mac 版 Kalodata 登录器……")
    print(f"Cookie 文件：{COOKIE_PATH}")

    context: BrowserContext | None = None
    try:
        with sync_playwright() as playwright:
            context = launch_context(playwright)

            extension_id = wait_for_extension_id(context)
            if extension_id:
                print(f"Kalodata 扩展已加载：{extension_id}")
            elif (EXTENSION_DIR / "manifest.json").is_file():
                print("警告：未检测到 Kalodata 扩展后台服务。")

            activation_code = os.getenv("KALODATA_ACTIVATION_CODE", "").strip()
            if activation_code:
                if not extension_id:
                    raise RuntimeError("扩展未成功加载，无法激活。")
                activate_and_login_extension(context, extension_id, activation_code)

            saved_cookie = read_saved_cookie()
            parsed_cookies = parse_cookie_header(saved_cookie) if saved_cookie else []
            if parsed_cookies:
                context.add_cookies(parsed_cookies)
                print(f"已向 Chrome 注入 {len(parsed_cookies)} 项 Cookie。")
            else:
                print("没有可用的本地 Cookie，请在浏览器中正常登录 Kalodata。")

            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(1_500)
                print(f"当前页面：{page.title().strip() or page.url}")
            except PlaywrightError as exc:
                print(f"页面首次打开未完成：{exc}")
                print("浏览器仍会保留，可在地址栏重新打开 Kalodata。")

            print("浏览器已打开。首次使用时请手工登录；登录后等待3秒即可保存 Cookie。")
            print("关闭这个专用 Chrome 窗口后，登录器会自动退出。")

            while not _stop_requested:
                try:
                    if not context.pages:
                        break
                    time.sleep(SAVE_INTERVAL_SECONDS)
                    export_current_cookie(context)
                except Exception:
                    break

            try:
                export_current_cookie(context)
            except Exception:
                pass
    except KeyboardInterrupt:
        print("收到退出信号，正在关闭登录器……")
    except (PlaywrightError, RuntimeError) as exc:
        print(f"启动失败：{exc}")
        print("请确认已安装 Google Chrome，并重新运行 Mac 启动脚本。")
        return 1
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass

    print("登录器已退出，Cookie 已保存在本地。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
