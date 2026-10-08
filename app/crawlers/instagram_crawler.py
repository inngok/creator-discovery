import json
import os
import re
import asyncio
import random
import time
import instaloader
from typing import Optional, List
from app.core.models import IngestPayload, CreatorInfo, Post, AIEnrichment, Platform

# Global Instaloader instance (cached)
_ig_loader = None
_current_account_idx = 0

def get_instaloader_instance():
    global _ig_loader, _current_account_idx
    
    if _ig_loader is None:
        _ig_loader = instaloader.Instaloader(
            sleep=True,
            quiet=False,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Instagram 258.0.0.12.65"
        )
        
    accounts_file = os.path.join("data", "ig_accounts.json")
    if os.path.exists(accounts_file):
        try:
            with open(accounts_file, "r", encoding="utf-8") as f:
                accounts = json.load(f)
            
            # Chỉ đăng nhập nếu có dữ liệu account thực sự
            if accounts and len(accounts) > 0 and accounts[0].get("username") != "dien_username_acc_rac_vao_day":
                account = accounts[_current_account_idx % len(accounts)]
                username = account.get("username")
                password = account.get("password")
                
                try:
                    # Cố gắng load session (cookie) đã lưu để không phải login lại
                    session_file = os.path.join("data", f"ig_session_{username}")
                    _ig_loader.load_session_from_file(username, filename=session_file)
                    print(f"[Instagram] Đã nạp thành công Session Cookie của account: {username}")
                except FileNotFoundError:
                    if username and password:
                        print(f"[Instagram] Đang đăng nhập mới bằng account: {username}...")
                        _ig_loader.login(username, password)
                        session_file = os.path.join("data", f"ig_session_{username}")
                        _ig_loader.save_session_to_file(filename=session_file)
                        print(f"[Instagram] Đăng nhập thành công và đã lưu Session Cookie!")
                
                # Chuyển sang account tiếp theo cho vòng lặp sau (Xoay vòng Account)
                _current_account_idx += 1
        except Exception as e:
            print(f"[Instagram] Lỗi khi thiết lập tài khoản: {e}")
            
    return _ig_loader


def _crawl_instagram_profile_sync(username: str) -> Optional[IngestPayload]:
    username = username.strip()
    if "instagram.com" in username:
        match = re.search(r'instagram\.com/([^/?&#]+)', username)
        if match:
            username = match.group(1)
            
    if username.startswith('@'):
        username = username[1:]
        
    L = get_instaloader_instance()
    
    try:
        profile = instaloader.Profile.from_username(L.context, username)
    except instaloader.exceptions.ProfileNotExistsException:
        raise ValueError(f"Không tìm thấy profile Instagram: {username}")
    except Exception as e:
        # Nếu bị Rate Limit hoặc Block, reset lại loader để lần sau nó đổi acc khác
        global _ig_loader
        _ig_loader = None
        raise ValueError(f"Không thể cào Profile (Có thể đã bị Rate Limit hoặc chưa đăng nhập): {e}")

    followers = profile.followers
    if followers < 7000:
        print(f"Bỏ qua {username}: chỉ có {followers} followers (nhỏ hơn 7000)")
        raise ValueError(f"Bỏ qua {username}: chỉ có {followers} followers (nhỏ hơn 7000)")

    signature = profile.biography or ""
    
    # Dùng Regex bóc SĐT và Email
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', signature)
    public_email = email_match.group(0) if email_match else None
    
    phone_match = re.search(r'(?:\+?84|0)(?:\s|\.)?\d{2,3}(?:\s|\.)?\d{3}(?:\s|\.)?\d{3,4}', signature)
    public_phone = phone_match.group(0) if phone_match else None

    business_category = profile.business_category_name or ""
    
    creator_info = CreatorInfo(
        platform=Platform.INSTAGRAM if hasattr(Platform, 'INSTAGRAM') else "INSTAGRAM", 
        externalId=str(profile.userid),
        username=f"@{username}",
        displayName=profile.full_name or username,
        profileUrl=f"https://www.instagram.com/{username}",
        avatarUrl=profile.profile_pic_url,
        followers=followers,
        bio=f"{business_category} - {signature}" if business_category else signature,
        publicEmail=public_email,
        publicPhone=public_phone
    )
    
    recent_posts: List[Post] = []
    
    try:
        # Lấy 3 post gần nhất
        count = 0
        for post in profile.get_posts():
            if count >= 3:
                break
                
            caption = post.caption or ""
                
            recent_posts.append(Post(
                platformPostId=post.shortcode,
                postUrl=f"https://www.instagram.com/p/{post.shortcode}/",
                caption=caption,
                views=post.video_view_count if post.is_video else 0,
                likes=post.likes,
                comments=post.comments,
                shares=0, # Instagram API không public lượt share
                postedAt=post.date_utc.isoformat()
            ))
            count += 1
            
            # Anti-ban sleep giữa các post
            time.sleep(random.uniform(1.0, 3.0))
            
    except Exception as e:
        print(f"Lỗi khi cào bài viết của {username}: {e}")

    # Anti-ban sleep sau khi cào xong 1 profile
    time.sleep(random.uniform(10.0, 20.0))

    # Đã tắt AI Enrichment theo yêu cầu (Cào thô)
    ai_enrichment = None

    return IngestPayload(
        creator=creator_info,
        recentPosts=recent_posts,
        aiEnrichment=ai_enrichment,
        source="INSTALOADER_WITH_ACCOUNT_ROTATION"
    )

async def crawl_instagram_profile(username: str) -> Optional[IngestPayload]:
    return await asyncio.to_thread(_crawl_instagram_profile_sync, username)
