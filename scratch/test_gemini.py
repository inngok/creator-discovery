import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv("d:\\creator-discovery\\.env")
api_key = os.environ.get("GEMINI_API_KEY")

if not api_key:
    print("No GEMINI_API_KEY found.")
    exit(1)

genai.configure(api_key=api_key)
model = genai.GenerativeModel('gemini-3.8-flash')

try:
    response = model.generate_content("Say test")
    print(response.text)
except Exception as e:
    print(f"Error calling Gemini: {type(e).__name__} - {str(e)}")
