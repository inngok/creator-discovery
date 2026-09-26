import traceback
import sys
import asyncio
from app.crawlers.tiktok_crawler import _crawl_tiktok_profile_sync

def test():
    print("Testing crawler...")
    try:
        res = _crawl_tiktok_profile_sync('halinhofficial')
        print(f"Result: {res}")
    except Exception as e:
        print("EXCEPTION IN MAIN THREAD:")
        traceback.print_exc()

if __name__ == "__main__":
    test()
