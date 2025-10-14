import os
from pathlib import Path
from dotenv import load_dotenv

# Tự động load .env.local nếu có
load_dotenv(".env.local")
load_dotenv()  # fallback .env

# Đường dẫn file key GCP (được import bên ngoài theo yêu cầu của bạn)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOOGLE_KEY_PATH = str(PROJECT_ROOT / "google_key.json")

GOOGLE_KEY_PATH = os.getenv("GOOGLE_KEY_PATH", DEFAULT_GOOGLE_KEY_PATH)

def get_env(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.getenv(name, default)
    if required and not val:
        raise RuntimeError(f"Missing required env: {name}")
    return val

def livekit_env():
    return {
        "url": get_env("LIVEKIT_URL", required=True),
        "api_key": get_env("LIVEKIT_API_KEY", required=True),
        "api_secret": get_env("LIVEKIT_API_SECRET", required=True),
    }

def google_env():
    return {
        "api_key": get_env("GOOGLE_API_KEY", required=True),
        "credentials_file": GOOGLE_KEY_PATH,
    }
