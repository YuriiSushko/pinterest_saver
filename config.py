import os
from dotenv import load_dotenv

def load_config() -> str:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is missing")
    return token
