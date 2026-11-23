"""Anonymous browser contexts shared by manual collection and scheduled workers.

The site may set visitor cookies during a request. They remain in memory and are
discarded when the browser closes; account cookies and disk sessions are not read.
"""


def create_public_context(playwright, platform: str):
    browser = playwright.chromium.launch(headless=True, args=[
        "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
    ])
    version = browser.version
    if not isinstance(version, str):
        version = "131.0.0.0"
    options = {"viewport": {"width": 1280, "height": 800}, "locale": "vi-VN",
               "user_agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36"}
    try:
        return browser.new_context(**options)
    except Exception:
        browser.close()
        raise
