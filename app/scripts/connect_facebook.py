"""Connect the crawler to Facebook through an interactive, user-controlled login.

Run from crawler root: venv\\Scripts\\python.exe app\\scripts\\connect_facebook.py
No password or cookie values are accepted as command arguments or printed.
"""
import argparse
import json
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = ROOT / 'data' / 'playwright_fb_state.json'


def has_login(cookies):
    names = {cookie.get('name') for cookie in cookies
             if cookie.get('expires', -1) < 0 or cookie.get('expires', 0) > time.time()}
    return {'c_user', 'xs'}.issubset(names)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    if args.check:
        state = json.loads(STATE_PATH.read_text(encoding='utf-8')) if STATE_PATH.exists() else {}
        print('FACEBOOK_SESSION_READY=' + str(has_login(state.get('cookies', []))))
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(locale='vi-VN', viewport={'width': 1200, 'height': 850})
        page = context.new_page()
        try:
            page.goto('https://www.facebook.com/login/', wait_until='domcontentloaded', timeout=30000)
            print('Log in directly in the Facebook window. Credentials stay in the browser.', flush=True)
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                if not browser.is_connected() or page.is_closed():
                    raise RuntimeError('Login window was closed; no session was saved.')
                if has_login(context.cookies('https://www.facebook.com')) and '/checkpoint' not in page.url:
                    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
                    temporary = STATE_PATH.with_suffix('.tmp')
                    context.storage_state(path=str(temporary))
                    os.replace(temporary, STATE_PATH)
                    print('FACEBOOK_SESSION_READY=True; saved locally in ignored data directory.', flush=True)
                    return
                page.wait_for_timeout(1000)
            raise TimeoutError('Login timed out; no session was saved.')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
