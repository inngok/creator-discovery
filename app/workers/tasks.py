import json
from .celery_app import app
from app.crawlers.tiktok_crawler import _crawl_tiktok_profile_sync

@app.task(bind=True, max_retries=3)
def process_tiktok_profile(self, username: str):
    print(f"[WORKER] Starting crawl for: {username}")
    try:
        # Call the synchronous crawling function directly
        result = _crawl_tiktok_profile_sync(username)
        
        if result:
            # BỘ LỌC KOC/KOL VIP: Chỉ lưu những người có trên 100.000 Followers
            if result.creator.followers >= 100000:
                # LƯU TẠM DỮ LIỆU ĐỂ DỄ KIỂM TRA
                with open("data/crawled_results.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(result.model_dump(mode='json'), ensure_ascii=False) + "\n")
                    
                # In ra màn hình (bỏ qua lỗi font chữ tiếng Việt trên Terminal)
                safe_category = str(result.aiEnrichment.tags if result.aiEnrichment else []).encode('ascii', 'ignore').decode()
                print(f"[WORKER] SUCCESS (KOL/KOC): {username} - {result.creator.followers} Followers. Niche: {safe_category}")
            else:
                print(f"[WORKER] SKIPPED (Bỏ qua): {username} chỉ có {result.creator.followers} Followers (không phải KOL).")
                
            # Later: POST to Java backend using httpx/requests
            return result.model_dump(mode='json')
        else:
            print(f"[WORKER] FAILED: Could not crawl {username}. Retrying...")
            raise self.retry(countdown=10)
    except Exception as e:
        print(f"[WORKER] ERROR processing {username}: {e}")
        raise self.retry(exc=e, countdown=10)
