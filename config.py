import os
from dotenv import load_dotenv

load_dotenv()

KROGER_CLIENT_ID = os.getenv("KROGER_CLIENT_ID", "")
KROGER_CLIENT_SECRET = os.getenv("KROGER_CLIENT_SECRET", "")
KROGER_BASE_URL = "https://api.kroger.com/v1"
