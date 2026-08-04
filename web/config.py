# config.py
# Configuration constants for the Backlogia application

import os
from pathlib import Path

# Database path - can be overridden by environment variable
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", Path(__file__).parent.parent / "data" / "game_library.db"))

# Authentication (optional) - disabled by default
ENABLE_AUTH = os.environ.get("ENABLE_AUTH", "false").lower() == "true"
SECRET_KEY = os.environ.get("SECRET_KEY", "")
SESSION_EXPIRY_DAYS = int(os.environ.get("SESSION_EXPIRY_DAYS", "30"))
