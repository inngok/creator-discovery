import os
import requests
from dotenv import load_dotenv

load_dotenv("d:\\creator-discovery\\.env")

def test_rapidapi():
    rapidapi_key = os.environ.get("RAPIDAPI_KEY")
    if not rapidapi_key:
        print("No key found!")
        return
        
    external_id = "72357433530"
    rapid_url = "https://tiktok-scraper7.p.rapidapi.com/user/posts"
    querystring = {"cursor":"0","user_id":external_id,"count":"3","sort_type":"0"}
    
    headers = {
        "x-rapidapi-key": rapidapi_key,
        "x-rapidapi-host": "tiktok-scraper7.p.rapidapi.com"
    }
    
    print(f"Using key: {rapidapi_key}")
    response = requests.get(rapid_url, headers=headers, params=querystring)
    
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        videos = data.get("data", {}).get("videos", [])
        print(f"Got {len(videos)} videos.")
        for v in videos[:1]:
            print(v.get("title"))
    else:
        print("Error:", response.text)

if __name__ == "__main__":
    test_rapidapi()
