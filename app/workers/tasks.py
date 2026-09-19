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
            with open("data/crawled_results.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(result.model_dump(mode='json'), ensure_ascii=False) + "\n")
                
            safe_category = str(result.aiEnrichment.tags if result.aiEnrichment else []).encode('ascii', 'ignore').decode()
            print(f"[WORKER] SUCCESS: {username} - {result.creator.followers} Followers. Type: {getattr(result.aiEnrichment, 'creatorType', 'UNKNOWN')}, Niche: {safe_category}")
            
            import httpx
            import os
            
            java_backend_url = os.environ.get("JAVA_BACKEND_URL", "http://localhost:8080/internal/creators/ingest")
            
            try:
                response = httpx.post(
                    java_backend_url, 
                    json=result.model_dump(mode='json'),
                    timeout=15.0
                )
                if response.status_code not in [200, 201]:
                    print(f"[WORKER] Failed to POST to backend. Status: {response.status_code}")
            except Exception as post_err:
                print(f"[WORKER] Failed to connect to backend: {post_err}")

            return result.model_dump(mode='json')
        else:
            print(f"[WORKER] FAILED: Could not crawl {username}. Retrying...")
            raise self.retry(countdown=10)
    except Exception as e:
        print(f"[WORKER] ERROR processing {username}: {e}")
        raise self.retry(exc=e, countdown=10)
