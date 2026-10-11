import json
from .celery_app import app
from app.crawlers.tiktok_crawler import _crawl_tiktok_profile_sync
from app.crawlers.facebook_crawler import _crawl_facebook_profile_sync
from app.ai.analyzer import process_ai_enrichment

@app.task(bind=True, max_retries=3)
def process_tiktok_profile(self, username: str):
    print(f"[WORKER] Starting crawl for: {username}")
    try:
        # Call the synchronous crawling function directly
        result = _crawl_tiktok_profile_sync(username)
        
        if result:
            # Chạy AI ở background worker thay vì Uvicorn
            result.aiEnrichment = process_ai_enrichment(result.creator, result.recentPosts)
            
            with open("data/crawled_results.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(result.model_dump(mode='json'), ensure_ascii=False) + "\n")
                
            safe_category = str(result.aiEnrichment.tags if result.aiEnrichment else []).encode('ascii', 'ignore').decode()
            print(f"[WORKER] SUCCESS: {username} - {result.creator.followers} Followers. Type: {getattr(result.aiEnrichment, 'creatorType', 'UNKNOWN')}, Niche: {safe_category}")
            
            import httpx
            import os
            
            java_backend_url = os.environ.get("JAVA_BACKEND_URL", "http://localhost:8080/api/v1/creators/ingest")
            
            # Tính toán chỉ số trung bình (Bỏ qua null theo Section 13)
            def calc_avg(posts, attr):
                valid_vals = [getattr(p, attr) for p in posts if getattr(p, attr) is not None]
                return sum(valid_vals) / len(valid_vals) if valid_vals else 0.0

            avg_views = calc_avg(result.recentPosts, 'views')
            avg_likes = calc_avg(result.recentPosts, 'likes')
            avg_comments = calc_avg(result.recentPosts, 'comments')
            avg_shares = calc_avg(result.recentPosts, 'shares')
            
            followers = result.creator.followers
            er = ((avg_likes + avg_comments + avg_shares) / followers * 100) if followers > 0 else 0.0
            
            recent_posts_list = []
            for p in result.recentPosts:
                recent_posts_list.append({
                    "platformPostId": p.platformPostId,
                    "postUrl": p.postUrl,
                    "caption": p.caption,
                    "views": p.views or 0,
                    "likes": p.likes or 0,
                    "comments": p.comments or 0,
                    "shares": p.shares or 0,
                    "postedAt": p.postedAt or ""
                })
            
            java_payload = {
                "platform": result.creator.platform.value,
                "externalId": result.creator.externalId,
                "userName": result.creator.username,
                "displayName": result.creator.displayName,
                "avatarUrl": result.creator.avatarUrl,
                "creatorType": getattr(result.aiEnrichment, 'creatorType', 'UNKNOWN'),
                "location": "Vietnam",
                "profileUrl": result.creator.profileUrl,
                "followers": followers,
                "avgViews": avg_views,
                "avgLikes": avg_likes,
                "avgComments": avg_comments,
                "avgShares": avg_shares,
                "engagementRate": er,
                "niche": result.aiEnrichment.niche if result.aiEnrichment else "General",
                "contentSummary": result.creator.bio or "Creator",
                "contact": getattr(result.creator, 'publicEmail', None) or "",
                "category": result.aiEnrichment.category if result.aiEnrichment else "Lifestyle",
                "freshnessStatus": "FRESH",
                "dataConfidence": 0.95,
                "recentPosts": recent_posts_list
            }
            
            try:
                response = httpx.post(
                    java_backend_url, 
                    json=java_payload,
                    headers={"X-Service-Key": "secret-key-123"},
                    timeout=15.0
                )
                if response.status_code not in [200, 201]:
                    print(f"[WORKER] Failed to POST to backend. Status: {response.status_code}, Msg: {response.text}")
                else:
                    print(f"[WORKER] Successfully sent to Java for {username}!")
            except Exception as post_err:
                print(f"[WORKER] Failed to connect to backend: {post_err}")

            return result.model_dump(mode='json')
        else:
            print(f"[WORKER] FAILED: Could not crawl {username}. Retrying...")
            raise self.retry(countdown=10)
    except Exception as e:
        print(f"[WORKER] ERROR processing {username}: {e}")
        raise self.retry(exc=e, countdown=10)

@app.task(bind=True, max_retries=3)
def process_facebook_profile(self, username: str):
    print(f"[WORKER] Starting Facebook crawl for: {username}")
    try:
        result = _crawl_facebook_profile_sync(username)
        
        if result:
            with open("data/crawled_results.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(result.model_dump(mode='json'), ensure_ascii=False) + "\n")
                
            safe_category = str(result.aiEnrichment.tags if result.aiEnrichment else []).encode('ascii', 'ignore').decode()
            print(f"[WORKER] SUCCESS FB: {username} - {result.creator.followers} Followers. Type: {getattr(result.aiEnrichment, 'creatorType', 'UNKNOWN')}, Niche: {safe_category}")
            
            import httpx
            import os
            
            java_backend_url = os.environ.get("JAVA_BACKEND_URL", "http://localhost:8080/api/v1/creators/ingest")
            
            def calc_avg(posts, attr):
                valid_vals = [getattr(p, attr) for p in posts if getattr(p, attr) is not None]
                return sum(valid_vals) / len(valid_vals) if valid_vals else 0.0

            avg_views = calc_avg(result.recentPosts, 'views')
            avg_likes = calc_avg(result.recentPosts, 'likes')
            avg_comments = calc_avg(result.recentPosts, 'comments')
            avg_shares = calc_avg(result.recentPosts, 'shares')
            
            followers = result.creator.followers
            er = ((avg_likes + avg_comments + avg_shares) / followers * 100) if followers > 0 else 0.0
            
            recent_posts_list = []
            for p in result.recentPosts:
                recent_posts_list.append({
                    "platformPostId": p.platformPostId,
                    "postUrl": p.postUrl,
                    "caption": p.caption,
                    "views": p.views or 0,
                    "likes": p.likes or 0,
                    "comments": p.comments or 0,
                    "shares": p.shares or 0,
                    "postedAt": p.postedAt or ""
                })
            
            java_payload = {
                "platform": result.creator.platform.value,
                "externalId": result.creator.externalId,
                "userName": result.creator.username,
                "displayName": result.creator.displayName,
                "avatarUrl": result.creator.avatarUrl,
                "creatorType": getattr(result.aiEnrichment, 'creatorType', 'UNKNOWN'),
                "location": "Vietnam",
                "profileUrl": result.creator.profileUrl,
                "followers": followers,
                "avgViews": avg_views,
                "avgLikes": avg_likes,
                "avgComments": avg_comments,
                "avgShares": avg_shares,
                "engagementRate": er,
                "niche": result.aiEnrichment.niche if result.aiEnrichment else "General",
                "contentSummary": result.creator.bio or "Creator",
                "contact": getattr(result.creator, 'publicEmail', None) or getattr(result.creator, 'publicPhone', None) or "",
                "category": result.aiEnrichment.category if result.aiEnrichment else "Lifestyle",
                "freshnessStatus": "FRESH",
                "dataConfidence": 0.95,
                "recentPosts": recent_posts_list
            }
            
            try:
                response = httpx.post(
                    java_backend_url, 
                    json=java_payload,
                    headers={"X-Service-Key": "secret-key-123"},
                    timeout=15.0
                )
                if response.status_code not in [200, 201]:
                    print(f"[WORKER] Failed to POST FB to backend. Status: {response.status_code}, Msg: {response.text}")
                else:
                    print(f"[WORKER] Successfully sent FB to Java for {username}!")
            except Exception as post_err:
                print(f"[WORKER] Failed to connect to backend: {post_err}")

            return result.model_dump(mode='json')
        else:
            print(f"[WORKER] FAILED: Could not crawl Facebook {username}. Retrying...")
            raise self.retry(countdown=10)
    except Exception as e:
        print(f"[WORKER] ERROR processing Facebook {username}: {e}")
        raise self.retry(exc=e, countdown=10)

@app.task(bind=True)
def run_retrain_pipeline(self):
    print("[WORKER] Bắt đầu chạy Vòng lặp học tập (AI Retrain Pipeline)...")
    try:
        from app.scripts.auto_update_prototypes import update_prototypes
        update_prototypes()
        print("[WORKER] Chạy thành công AI Retrain Pipeline.")
    except Exception as e:
        print(f"[WORKER] Lỗi khi chạy AI Retrain Pipeline: {e}")
