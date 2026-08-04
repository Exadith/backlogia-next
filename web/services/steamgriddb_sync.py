# steamgriddb_sync.py
# Fetches SteamGridDB covers for games in our database

import sqlite3
import requests
import time
import threading
import re

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from .image_cache import download_cover

COVERS_DIR = Path("/data/covers")
COVERS_DIR.mkdir(parents=True, exist_ok=True)



class SteamGridDBClient:
    """Client for SteamGridDB API."""

    BASE_URL = "https://www.steamgriddb.com/api/v2"

    def __init__(self, api_key, min_request_interval=0.2):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Backlogia/1.0",
            "Accept": "application/json",
        })

        self.last_request_time = 0
        self.min_request_interval = min_request_interval
        self._lock = threading.Lock()

    def _rate_limit(self):
        """Ensure we don't make requests too quickly (thread-safe)."""
        with self._lock:
            elapsed = time.time() - self.last_request_time
            if elapsed < self.min_request_interval:
                time.sleep(self.min_request_interval - elapsed)
            self.last_request_time = time.time()

    def _make_request(self, url):
        """Make a rate-limited request."""
        self._rate_limit()
        try:
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            print(f"Request error: {e}")
            return None


    def get_grids_by_steam_appid(self, steam_appid):
        """Return all grids for a Steam AppID."""

        url = f"{self.BASE_URL}/grids/steam/{steam_appid}"

        response = self._make_request(url)

        if not response:
            return []

        try:
            data = response.json()
        except Exception:
            return []

        if not data.get("success"):
            return []

        return data.get("data", [])

    @staticmethod
    def select_best_grid(grids):
        """Choose the best vertical cover."""

        if not grids:
            return None

        vertical = [
            g for g in grids
            if g.get("height", 0) > g.get("width", 0)
        ]

        if not vertical:
            return None

        preferred = [
            g for g in vertical
            if g.get("language") == "en"
            and g.get("style") == "alternate"
        ]
        
        return preferred[0] if preferred else vertical[0]


    def search_game(self, name):
        """Search SteamGridDB by game name."""

        url = f"{self.BASE_URL}/search/autocomplete/{requests.utils.quote(name)}"

        response = self._make_request(url)

        if not response:
            return None

        try:
            data = response.json()
        except Exception:
            return None

        if not data.get("success"):
            return None

        games = data.get("data", [])

        if not games:
            return None

        for game in games:
            if game["name"].lower() == name.lower():
                return game
        
        search = normalize_name(name).casefold()
        
        for game in games:
            if normalize_name(game["name"]).casefold() == search:
                return game
        
        return games[0] if games else None

    def get_grids_by_gameid(self, game_id):
        url = f"{self.BASE_URL}/grids/game/{game_id}"

        response = self._make_request(url)

        if not response:
            return []

        try:
            data = response.json()
        except Exception:
            return []

        if not data.get("success"):
            return []

        return data.get("data", [])

def add_steamgriddb_columns(conn):
    pass

def normalize_name(name: str) -> str:
    # usuń znaki towarowe
    name = re.sub(r"[®™©]", "", name)
    # zamień wielokrotne spacje na jedną
    name = re.sub(r"\s+", " ", name)
    return name.strip()

def _process_single_game(
    client,
    game_id,
    name,
    store,
    store_id,
    steam_app_id,
):
    try:
        #
        # 1. Najpierw próbujemy pobrać oficjalne assety Steam
        #
        if store == "steam" and store_id:

            cover_url = (
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/"
                f"{store_id}/library_600x900_2x.jpg"
            )

            hero_url = (
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/"
                f"{store_id}/library_hero.jpg"
            )

            assets = download_cover(
                cover_url,
                game_id,
                background_url=hero_url,
            )

            if assets:
                print("Using official Steam assets")

                return (
                    game_id,
                    True,
                    {
                        "cover_file": assets["cover"],
                        "background_file": assets["background"],
                    },
                )

            print("Steam assets missing, falling back to SteamGridDB...")

        #
        # 2. SteamGridDB fallback
        #
        grids = []

        if steam_app_id:
            grids = client.get_grids_by_steam_appid(steam_app_id)

        if not grids:
            game = client.search_game(normalize_name(name))
            if game:
                grids = client.get_grids_by_gameid(game["id"])

        best = client.select_best_grid(grids)

        if not best:
            return (
                game_id,
                False,
                "No vertical grid found",
            )

        print(best["url"])

        background_url = None

        if store == "steam" and store_id:
            background_url = (
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/"
                f"{store_id}/library_hero.jpg"
            )

        assets = download_cover(
            best["url"],
            game_id,
            background_url=background_url,
        )

        if not assets:
            return (
                game_id,
                False,
                "Failed to download cover",
            )

        return (
            game_id,
            True,
            {
                "cover_file": assets["cover"],
                "background_file": assets["background"],
            },
        )

    except Exception as e:
        return (
            game_id,
            False,
            str(e),
        )
    
def sync_games(conn, client, limit=None, force=False, max_workers=5, progress_callback=None):
    """Sync SteamGridDB covers.

    Args:
        conn: Database connection
        client: SteamGridDBClient instance
        limit: Maximum number of games to process
        force: If True, resync all games; if False, only sync games without a SteamGridDB cover
        max_workers: Number of parallel workers
        progress_callback: Optional callback(current, total, message)
    """
    cursor = conn.cursor()

    # Get games that haven't been matched yet (or all if force)
    # Skip hidden games and deduplicate by name (for games owned on multiple stores)
    if force:
        cursor.execute(
            """SELECT
                    MIN(id) as id,
                    name,
                    store,
                    store_id,
                    steam_app_id
                FROM games
                WHERE name IS NOT NULL
                AND (hidden IS NULL OR hidden = 0)
                GROUP BY LOWER(name)
                ORDER BY name"""
        )
    else:
        cursor.execute(
            """SELECT
                    MIN(id) as id,
                    name,
                    store,
                    store_id,
                    steam_app_id
                FROM games
                WHERE name IS NOT NULL
                AND steamgriddb_cover_url IS NULL
                AND (hidden IS NULL OR hidden = 0)
                GROUP BY LOWER(name)
                ORDER BY name"""
        )

    games = cursor.fetchall()

    if limit is not None:
        games = games[:limit]

    total = len(games)
    print(f"Processing {total} games for SteamGridDB covers with {max_workers} workers...")

    matched = 0
    failed = 0
    completed = 0
    results_lock = threading.Lock()

    def update_database(game_id, name, result):
        """Update SteamGridDB cover for all copies of the game."""
    
        cursor.execute(
            """
            UPDATE games
            SET steamgriddb_cover_url = ?,
                background_image = COALESCE(?, background_image)
            WHERE LOWER(name) = LOWER(?)
            """,
            (
                result["cover_file"],
                result["background_file"],
                name,
            ),
        )

    def mark_not_found(name):
        pass

    # Process games in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_game = {
            executor.submit(
                _process_single_game,
                client,
                game_id,
                name,
                store,
                store_id,
                steam_app_id,
            ): (game_id, name)
            for game_id, name, store, store_id, steam_app_id in games
        }

        # Process results as they complete
        for future in as_completed(future_to_game):
            game_id, name = future_to_game[future]
            completed += 1

            # Report progress
            if progress_callback:
                progress_callback(completed, total, f"Processing: {name[:50]}...")

            try:
                result_game_id, success, result = future.result()

                if success:
                    # Update database (SQLite operations need to be serialized)
                    with results_lock:
                        update_database(result_game_id, name, result)
                        conn.commit()
                        matched += 1

                    print(f"[{completed}/{total}] {name} → Cover found")
                else:
                    # Mark as searched but not found
                    with results_lock:
                        mark_not_found(name)
                        conn.commit()
                        failed += 1
                    print(f"[{completed}/{total}] {name} → {result}")

            except Exception as e:
                # Mark as searched but not found on exception
                with results_lock:
                    mark_not_found(name)
                    conn.commit()
                    failed += 1
                print(f"[{completed}/{total}] {name} → Exception: {e}")

    return matched, failed


def get_stats(conn):
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*)
        FROM games
        WHERE steamgriddb_cover_url IS NOT NULL
    """)

    matched = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM games")
    total = cursor.fetchone()[0]

    return {
        "total": total,
        "matched": matched,
        "match_rate": (matched / total * 100) if total else 0,
    }