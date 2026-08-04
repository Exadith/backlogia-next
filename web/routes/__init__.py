# routes package
# FastAPI router exports

from .library import router as library_router
from .api_games import router as api_games_router
from .api_metadata import router as api_metadata_router
from .discover import router as discover_router
from .settings import router as settings_router
from .sync import router as sync_router
from .auth import router as auth_router
from .app_auth import router as app_auth_router
from .collections import router as collections_router

__all__ = [
    "library_router",
    "api_games_router",
    "api_metadata_router",
    "discover_router",
    "settings_router",
    "sync_router",
    "auth_router",
    "app_auth_router",
    "collections_router",
]
