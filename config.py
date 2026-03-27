"""
config.py — Central configuration
Loads from .env file automatically (fix #1)
"""
import os
from dotenv import load_dotenv

load_dotenv()  # ← Fix #1: actually load the .env file

from typing import List

# ─── Telegram API ──────────────────────────────────────────────────────────────
API_ID: int   = int(os.getenv("API_ID", "0"))
API_HASH: str = os.getenv("API_HASH", "")

# ─── Master bot token ──────────────────────────────────────────────────────────
# You only need ONE master bot token. Child bots are added via /addbot command.
MASTER_TOKEN: str = os.getenv("MASTER_TOKEN", "")

# ─── MongoDB ───────────────────────────────────────────────────────────────────
MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME: str   = os.getenv("DB_NAME", "multibot_system")

# ─── Admins ────────────────────────────────────────────────────────────────────
ADMINS: List[int] = list(map(int, os.getenv("ADMINS", "0").split()))

# ─── Broadcast performance ─────────────────────────────────────────────────────
BATCH_SIZE:   int   = int(os.getenv("BATCH_SIZE",   "80"))
CONCURRENCY:  int   = int(os.getenv("CONCURRENCY",  "15"))
MIN_DELAY:    float = float(os.getenv("MIN_DELAY",  "0.05"))
MAX_DELAY:    float = float(os.getenv("MAX_DELAY",  "0.15"))
RETRY_DELAY:  float = float(os.getenv("RETRY_DELAY","0.3"))

# ─── Anti-ban ──────────────────────────────────────────────────────────────────
BOT_RATE_LIMIT: int = int(os.getenv("BOT_RATE_LIMIT", "25"))  # msgs/sec per bot

# ─── Heartbeat ─────────────────────────────────────────────────────────────────
HEARTBEAT_INTERVAL: int = 60   # seconds between pings
HEARTBEAT_TIMEOUT:  int = 90   # seconds before marking bot offline

# ─── Sessions directory (Fix #4: file-based sessions, not in_memory) ──────────
SESSIONS_DIR: str = os.getenv("SESSIONS_DIR", "sessions")

# ─── Web dashboard ─────────────────────────────────────────────────────────────
WEB_HOST:         str = os.getenv("WEB_HOST",         "0.0.0.0")
WEB_PORT:         int = int(os.getenv("WEB_PORT",     "8080"))
DASHBOARD_TOKEN:  str = os.getenv("DASHBOARD_TOKEN",  "changeme123")

# ─── Log channel (Fix #15: auto-notify on broadcast complete) ──────────────────
# Set to your Telegram channel/group ID. Leave 0 to disable.
LOG_CHANNEL: int = int(os.getenv("LOG_CHANNEL", "0"))
