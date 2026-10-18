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
import os
from lib.kalodata_login import run_login  # noqa: E402


def main():
    return run_login(ROOT,os.environ.get('KALODATA_LOGIN_MODE','refresh'))


if __name__ == '__main__':
    raise SystemExit(main())
