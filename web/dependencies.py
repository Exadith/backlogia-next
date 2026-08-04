# dependencies.py
# FastAPI dependency injection for database connections

import sqlite3
from typing import Generator

from .config import DATABASE_PATH


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Database dependency that provides a connection and ensures cleanup.

    Usage:
        @router.get("/endpoint")
        def endpoint(conn: sqlite3.Connection = Depends(get_db)):
            cursor = conn.cursor()
            # ... use cursor ...
    """
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
