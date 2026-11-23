import re
import urllib.parse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

query = 'site:tiktok.com/@ "review mỹ phẩm"'
url = 'https://html.duckduckgo.com/html/?q=' + urllib.parse.quote(query)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(url, timeout=15000)
    soup = BeautifulSoup(page.content(), 'html.parser')
    usernames = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        m = re.search(r'tiktok\.com/@([\w.]+)', href)
        if m:
            usernames.add(m.group(1))
    print('Found TikTok usernames from DDG:', len(usernames), list(usernames)[:10])
    browser.close()
