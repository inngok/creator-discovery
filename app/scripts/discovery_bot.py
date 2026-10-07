import time
import undetected_chromedriver as uc
from bs4 import BeautifulSoup
from app.workers.tasks import process_tiktok_profile

def discover_creators_by_keyword(keyword: str):
    safe_kw = keyword.encode('ascii', 'ignore').decode()
    print(f"[DISCOVERY] Starting TikTok search for keyword: '{safe_kw}'...")
    options = uc.ChromeOptions()
    options.headless = True # Tàng hình
    
    try:
        driver = uc.Chrome(options=options)
    except:
        # Fallback for Chrome 152 if driver defaults to 153
        options2 = uc.ChromeOptions()
        options2.headless = True
        driver = uc.Chrome(options=options2, version_main=152)
    
    try:
        import urllib.parse
        encoded_keyword = urllib.parse.quote(keyword)
        # Vào trang Tìm kiếm Người dùng của TikTok
        driver.get(f"https://www.tiktok.com/search/user?q={encoded_keyword}")
        
        # Chờ trang load (Tìm kiếm thường load lâu hơn)
        time.sleep(7)
        
        usernames = set()
        
        # Scroll a few times to discover creators
        for i in range(5):
            print(f"[DISCOVERY] Scrolling {i+1}/5 to load more content...")
            driver.execute_script("window.scrollBy(0, 1500);")
            time.sleep(3)
            
            html = driver.page_source
            soup = BeautifulSoup(html, 'html.parser')
            
            # Find all profile links
            a_tags = soup.find_all('a')
            for a in a_tags:
                href = a.get('href', '')
                if href.startswith('/@'):
                    # Example: /@_xusothankinh?lang=en -> _xusothankinh
                    username = href.split('?')[0].replace('/@', '').strip()
                    if username and username not in usernames:
                        usernames.add(username)
                        print(f"[DISCOVERY] Found creator: {username}")
                        
                        # 🚀 Push to Redis Queue for Celery workers to pick up
                        process_tiktok_profile.delay(username)
                        
        print(f"[DISCOVERY] Completed! Sent {len(usernames)} tasks to the Queue.")
    except Exception as e:
        print(f"[DISCOVERY] Error: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    # Hệ thống sẽ tự động lướt qua đa dạng các ngành nghề
    diverse_keywords = [
        "review đồ ăn", "thời trang", "mỹ phẩm", "công nghệ", 
        "du lịch", "thể thao", "mẹ và bé", "giải trí"
    ]
    
    while True:
        for kw in diverse_keywords:
            discover_creators_by_keyword(kw)
            # Thêm thời gian nghỉ giữa các từ khóa (giãn cách ra 10 phút)
            print(f"[DISCOVERY] Đã quét xong từ khóa '{kw}'. Tạm nghỉ 10 phút để tránh bị chặn IP...")
            time.sleep(600)
            
        print("[DISCOVERY] Hoàn thành 1 vòng lớn. Sẽ ngủ 6 tiếng (21600s) trước khi chạy vòng tiếp theo...")
        time.sleep(21600)
