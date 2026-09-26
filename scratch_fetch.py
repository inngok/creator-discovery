import undetected_chromedriver as uc
import time
import json
import base64

def test_fetch():
    options = uc.ChromeOptions()
    options.headless = True
    options.add_argument('--disable-gpu')
    
    driver = uc.Chrome(options=options, version_main=153)
    try:
        # We need to use websocket to listen to Fetch.requestPaused because execute_cdp_cmd is synchronous and blocks.
        # undetected_chromedriver doesn't expose the websocket easily.
        # But wait! We can just use driver.get_log('performance')!
        # If we enable Fetch, it might show up in performance logs? No, Fetch domain events do not show in performance logs by default, only Network domain events.
        pass
    finally:
        driver.quit()

if __name__ == "__main__":
    test_fetch()
