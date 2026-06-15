import os
import logging
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("realestate-chat")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")


DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")

# URL-encode user/password so special characters (@ % ) { } = etc.) don't
# corrupt the connection string.
_USER = quote_plus(DB_USER) if DB_USER else ""
_PASSWORD = quote_plus(DB_PASSWORD) if DB_PASSWORD else ""

DATABASE_URL = (
    f"mysql+pymysql://{_USER}:{_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    "?charset=utf8mb4"
)
