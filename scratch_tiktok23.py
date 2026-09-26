import requests
import json

headers = {
    'x-rapidapi-key': 'ebeaffa470msh57db7c70d04e0dbp1284e2jsn7fa846ed587f', 
    'x-rapidapi-host': 'tiktok-api23.p.rapidapi.com'
}

# 1. Get User Info
info_res = requests.get('https://tiktok-api23.p.rapidapi.com/api/user/info?uniqueId=halinhofficial', headers=headers)
info_data = info_res.json()

user = info_data.get('userInfo', {}).get('user', {})
uid = user.get('id')
sec_uid = user.get('secUid')
print(f"UID: {uid}, SecUID: {sec_uid}")

# 2. Try getting posts with secUid
if sec_uid:
    posts_res = requests.get(f'https://tiktok-api23.p.rapidapi.com/api/user/posts?secUid={sec_uid}&count=3&cursor=0', headers=headers)
    print("Posts status:", posts_res.status_code)
    try:
        posts_data = posts_res.json()
        print("Posts keys:", posts_data.keys())
        if 'itemList' in posts_data:
            print("Found", len(posts_data['itemList']), "posts")
    except Exception as e:
        print("Error parsing posts JSON:", e)

# 3. Try with uid if secUid fails or returns 204
if uid:
    posts_res2 = requests.get(f'https://tiktok-api23.p.rapidapi.com/api/user/posts?uid={uid}&count=3&cursor=0', headers=headers)
    print("Posts (UID) status:", posts_res2.status_code)
