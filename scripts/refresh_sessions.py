"""
Script login thủ công để refresh session TikTok và Facebook.
Chạy: python scripts/refresh_sessions.py

Sẽ mở cửa sổ trình duyệt để bạn login thủ công,
sau đó tự động lưu session vào data/playwright_state.json và data/playwright_fb_state.json.
"""
import os
import sys
import time

# Chạy từ thư mục gốc dự án
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

from playwright.sync_api import sync_playwright

TIKTOK_STATE = os.path.join("data", "playwright_state.json")
FB_STATE = os.path.join("data", "playwright_fb_state.json")
IG_STATE = os.path.join("data", "playwright_ig_state.json")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def refresh_tiktok():
    print("\n=== REFRESH SESSION TIKTOK ===")
    print("Trình duyệt sẽ mở ra. Hãy đăng nhập TikTok rồi nhấn Enter ở đây.")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 720},
        )
        page = context.new_page()
        page.goto("https://www.tiktok.com/login", wait_until="domcontentloaded")
        input("  --> Sau khi đăng nhập xong và thấy trang chính TikTok, nhấn Enter tại đây: ")
        # Lưu session
        active = {c["name"] for c in context.cookies("https://www.tiktok.com/")}
        if "sessionid" not in active:
            browser.close()
            raise RuntimeError("TikTok chua dang nhap; khong ghi de session cu.")
        os.makedirs("data", exist_ok=True)
        context.storage_state(path=TIKTOK_STATE)
        print(f"  Đã lưu session TikTok vào {TIKTOK_STATE}")
        browser.close()


def refresh_facebook():
    print("\n=== REFRESH SESSION FACEBOOK ===")
    print("Trình duyệt sẽ mở ra. Hãy đăng nhập Facebook rồi nhấn Enter ở đây.")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 800},
            locale="vi-VN",
        )
        page = context.new_page()
        page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
        input("  --> Sau khi đăng nhập xong và thấy News Feed Facebook, nhấn Enter tại đây: ")
        active = {c["name"] for c in context.cookies("https://www.facebook.com/")}
        if not {"c_user", "xs"}.issubset(active):
            browser.close()
            raise RuntimeError("Facebook chua dang nhap; khong ghi de session cu.")
        os.makedirs("data", exist_ok=True)
        context.storage_state(path=FB_STATE)
        print(f"  Đã lưu session Facebook vào {FB_STATE}")
        browser.close()


def refresh_instagram():
    print("Dang mo Instagram. Dang nhap trong trinh duyet, sau do nhan Enter tai day.")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            context = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 800})
            page = context.new_page()
            page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
            input("Dang nhap xong, nhan Enter: ")
            active = {c["name"] for c in context.cookies("https://www.instagram.com/")}
            if not {"sessionid", "ds_user_id"}.issubset(active):
                raise RuntimeError("Instagram chua dang nhap; khong ghi de session cu.")
            os.makedirs("data", exist_ok=True)
            context.storage_state(path=IG_STATE)
            print("Da luu session Instagram.")
        finally:
            browser.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=["tiktok", "facebook", "instagram", "all"], default="all",
                        help="Platform cần refresh session (default: all)")
    args = parser.parse_args()

    if args.platform in ("tiktok", "all"):
        refresh_tiktok()
    if args.platform in ("facebook", "all"):
        refresh_facebook()
    if args.platform in ("instagram", "all"):
        refresh_instagram()

    print("\nDone. Chạy lại thu thập để kiểm tra.")
