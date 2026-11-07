import os
from dotenv import load_dotenv

load_dotenv()

KROGER_CLIENT_ID = os.getenv("KROGER_CLIENT_ID", "")
KROGER_CLIENT_SECRET = os.getenv("KROGER_CLIENT_SECRET", "")
KROGER_REDIRECT_URI = os.getenv("KROGER_REDIRECT_URI", "http://localhost:8000/callback")
KROGER_BASE_URL = "https://api.kroger.com/v1"
