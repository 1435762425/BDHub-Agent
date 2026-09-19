#!/usr/bin/env python3
"""Refresh the Kalodata extension login using the card the extension already saved.

Run with the grabber's own interpreter, where Playwright lives:

    <grabber>/Kalodata登录器独立版/.venv-mac/bin/python scripts/kalodata-extension-refresh.py

``cookie_launcher.py`` supports activation from the command line but has no refresh entry point,
so this thin adapter imports it and calls the refresh path it already implements. It never reads,
prints, or copies the card or the cookie.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.kalodata_identity import export_helpers  # noqa: E402


def main():
    launcher = export_helpers()
    with launcher.sync_playwright() as playwright:
        context = launcher.launch_context(playwright)
        try:
            extension_id = launcher.wait_for_extension_id(context)
            if not extension_id:
                print('未检测到 Kalodata 扩展，无法刷新。')
                return 1
            if not launcher.refresh_extension_login(context, extension_id):
                print('扩展里没有已保存的卡号，需要先用激活码获取身份。')
                return 1
            launcher.export_extension_runtime(context, extension_id)
            launcher.export_current_cookie(context)
            print('身份已刷新，Cookie 与代理已更新。可以关闭这个 Chrome 窗口。')
            # Keep the context alive until the operator closes the window, exactly like the launcher.
            while context.pages:
                context.pages[0].wait_for_timeout(3000)
                launcher.export_current_cookie(context)
        finally:
            try:
                context.close()
            except Exception:
                pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
