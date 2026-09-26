import undetected_chromedriver as uc
import time
import json

def test_inject():
    options = uc.ChromeOptions()
    options.headless = True
    options.add_argument('--disable-gpu')
    
    driver = uc.Chrome(options=options, version_main=153)
    try:
        # Inject script BEFORE page loads
        script = """
        window.__captured_items = null;
        const originalFetch = window.fetch;
        window.fetch = async function(...args) {
            const response = await originalFetch.apply(this, args);
            try {
                const clone = response.clone();
                if (args[0] && typeof args[0] === 'string' && args[0].includes('/api/post/item_list')) {
                    clone.json().then(data => {
                        window.__captured_items = data;
                    }).catch(e => console.error(e));
                }
            } catch(e) {}
            return response;
        };
        
        // Also hook XMLHttpRequest just in case
        const originalXhrOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url) {
            this.addEventListener('load', function() {
                if (url.includes('/api/post/item_list')) {
                    try {
                        window.__captured_items = JSON.parse(this.responseText);
                    } catch(e) {}
                }
            });
            originalXhrOpen.apply(this, arguments);
        };
        """
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': script})
        
        driver.get("https://www.tiktok.com/@halinhofficial")
        
        for _ in range(3):
            driver.execute_script("window.scrollBy(0, 1500);")
            time.sleep(2)
            
        data = driver.execute_script("return window.__captured_items;")
        if data:
            print("FOUND DATA VIA INJECTION!")
            print(f"Item list length: {len(data.get('itemList', []))}")
        else:
            print("NO DATA FOUND VIA INJECTION!")
            
    finally:
        driver.quit()

if __name__ == "__main__":
    test_inject()
