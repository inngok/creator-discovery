"""Browser construction; the public wrapper always creates an anonymous context."""


def create_browser_context(playwright, storage_state=None):
    browser = playwright.chromium.launch(headless=True, args=[
        "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
    ])
    version = browser.version
    if not isinstance(version, str):
        version = "131.0.0.0"
    options = {"viewport": {"width": 1280, "height": 800}, "locale": "vi-VN",
               "user_agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36"}
    if storage_state is not None:
        options["storage_state"] = storage_state
    try:
        return browser.new_context(**options)
    except Exception:
        browser.close()
        raise


def create_public_context(playwright, platform: str):
    return create_browser_context(playwright)
