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
        
        for _ in range(3):
            driver.execute_script("window.scrollBy(0, 1500);")
            time.sleep(2)
            
        logs = driver.get_log('performance')
        found_urls = set()
        item_list_req_id = None
        
        for entry in logs:
            log = json.loads(entry['message'])['message']
            if log['method'] == 'Network.requestWillBeSent':
                url = log['params']['request']['url']
                if 'tiktok.com/api/' in url:
                    found_urls.add(url.split('?')[0])
                if '/api/post/item_list' in url:
                    item_list_req_id = log['params']['requestId']
                    
        print("API URLs called by TikTok:")
        for u in found_urls:
            print("-", u)
            
        if item_list_req_id:
            print(f"FOUND item_list request ID: {item_list_req_id}")
            # Try to fetch response for it if finished
            for entry in logs:
                log = json.loads(entry['message'])['message']
                if log['method'] == 'Network.loadingFinished' and log['params']['requestId'] == item_list_req_id:
                    print("LOADING FINISHED FOR item_list!")
                    res = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': item_list_req_id})
                    if res and 'body' in res:
                        print(f"BODY LENGTH: {len(res['body'])}")
        else:
            print("item_list API NOT FOUND IN LOGS!")
    finally:
        driver.quit()

if __name__ == "__main__":
    test_intercept()
