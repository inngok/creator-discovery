import undetected_chromedriver as uc
import time
import json

def test_intercept():
    options = uc.ChromeOptions()
    options.headless = True
    options.add_argument('--disable-gpu')
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    
    driver = uc.Chrome(options=options, version_main=153)
    try:
        driver.execute_cdp_cmd('Network.enable', {})
        driver.get("https://www.tiktok.com/@halinhofficial")
        time.sleep(5)
        
        logs = driver.get_log('performance')
        found = False
        for entry in logs:
            log = json.loads(entry['message'])['message']
            if log['method'] == 'Network.responseReceived':
                url = log['params']['response']['url']
                if '/api/post/item_list' in url:
                    print("FOUND ITEM_LIST:", url)
                    req_id = log['params']['requestId']
                    body = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': req_id})
                    print("BODY SNIPPET:", str(body)[:200])
                    found = True
                    break
        if not found:
            print("Not found.")
    finally:
        driver.quit()

if __name__ == "__main__":
    test_intercept()
