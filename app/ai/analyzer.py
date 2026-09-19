import os
from typing import List
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

# This configures the API key if it's present in the environment variables
# If it's not present, it won't crash on import, but will fail when called.
api_key = os.environ.get("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

def categorize_creator(bio: str, recent_videos: List[str]) -> List[str]:
    """
    Analyzes the creator's bio and recent videos using Gemini AI 
    to categorize their content.
    Returns a list of up to 3 category tags.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or api_key == "your_api_key_here":
        print("Warning: GEMINI_API_KEY is not set or invalid. Skipping AI categorization.")
        return []
    
    # Configure in case it was loaded later
    genai.configure(api_key=api_key)
    
    prompt = f"""
    You are an expert at analyzing TikTok/YouTube creator profiles. 
    Analyze the following creator profile and classify them into 1 to 3 content categories.
    Return ONLY a comma-separated list of categories in Vietnamese (e.g., Công nghệ, Thời trang, Ẩm thực, Giải trí, Giáo dục, Đời sống).
    Do not return any other text.
    
    Creator Bio:
    {bio}
    
    Recent Video Titles/Descriptions:
    {chr(10).join(recent_videos) if recent_videos else "N/A"}
    """
    try:
        # Use gemini-2.5-flash for fast and cost-effective text tasks
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(prompt)
        categories_text = response.text.strip()
        
        # Parse comma separated string into list
        categories = [cat.strip() for cat in categories_text.split(',') if cat.strip()]
        return categories[:3]
    except Exception as e:
        print(f"Gemini AI Error: {e}")
        return []
