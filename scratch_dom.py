import undetected_chromedriver as uc
import time
from bs4 import BeautifulSoup

def test_dom():
    options = uc.ChromeOptions()
    options.headless = True
    options.add_argument('--disable-gpu')
    
    driver = uc.Chrome(options=options, version_main=153)
    try:
        driver.get("https://www.tiktok.com/@halinhofficial")
        
        for _ in range(3):
            driver.execute_script("window.scrollBy(0, 1500);")
            time.sleep(2)
            
        html = driver.page_source
        soup = BeautifulSoup(html, 'html.parser')
        
        posts = soup.select('[data-e2e="user-post-item"]')
        print(f"FOUND {len(posts)} posts in DOM!")
        for i, post in enumerate(posts[:3]):
            desc_tag = post.select_one('[data-e2e="user-post-item-desc"]')
            desc = desc_tag.text if desc_tag else "No desc"
            views_tag = post.select_one('[data-e2e="video-views"]')
            views = views_tag.text if views_tag else "0"
            print(f"Post {i+1}: {desc[:50]}... | Views: {views}")
            
    finally:
        driver.quit()

if __name__ == "__main__":
    test_dom()
