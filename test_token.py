# test_token.py
import requests
from dotenv import load_dotenv
import os

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getMe")
print(r.json())