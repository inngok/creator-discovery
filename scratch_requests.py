import undetected_chromedriver as uc
import time
import requests

def test_requests():
    options = uc.ChromeOptions()
    options.headless = True
    options.add_argument('--disable-gpu')
    
    driver = uc.Chrome(options=options, version_main=153)
    try:
        driver.get("https://www.tiktok.com/@halinhofficial")
        time.sleep(3)
        
        cookies = driver.get_cookies()
        session = requests.Session()
        for cookie in cookies:
            session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'])
            
        user_agent = driver.execute_script("return navigator.userAgent;")
        
        # We need secUid! Let's get it from html
        html = driver.page_source
        import json
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        script = soup.find('script', id='__UNIVERSAL_DATA_FOR_REHYDRATION__')
        if not script:
            print("No script")
            return
        data = json.loads(script.string)
        secUid = data.get('__DEFAULT_SCOPE__', {}).get('webapp.user-detail', {}).get('userInfo', {}).get('user', {}).get('secUid', '')
        
        print("SECUID:", secUid)
        
        # Call API
        api_url = f"https://www.tiktok.com/api/post/item_list/?WebIdLastTime=1&aid=1988&app_language=en&app_name=tiktok_web&browser_language=en-US&browser_name=Mozilla&browser_online=true&browser_platform=Win32&browser_version=5.0&channel=tiktok_web&cookie_enabled=true&count=3&cursor=0&device_platform=web_pc&focus_state=true&history_len=3&is_fullscreen=false&is_page_visible=true&language=en&os=windows&priority_region=VN&referer=&region=VN&screen_height=1080&screen_width=1920&secUid={secUid}&tz_name=Asia%2FHo_Chi_Minh&webcast_language=en"
        
        headers = {
            'User-Agent': user_agent,
            'Accept': '*/*',
            'Referer': 'https://www.tiktok.com/@halinhofficial',
            'Connection': 'keep-alive'
        }
        
        res = session.get(api_url, headers=headers)
        print("STATUS:", res.status_code)
        if res.status_code == 200:
            print("BODY:", str(res.json())[:300])
        else:
            print(res.text)
            
    finally:
        driver.quit()

if __name__ == "__main__":
    test_requests()
