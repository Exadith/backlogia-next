# steam.py
# Fetches games from Steam library

import requests
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from ..services.settings import get_steam_credentials

# Rate limiting for Steam Store API
_rate_limit_lock = Lock()
_last_request_time = 0
_MIN_REQUEST_INTERVAL = 0.2  # 200ms between requests (5 req/sec max)


def _rate_limited_request(url, params=None):
    """Make a rate-limited request to Steam Store API."""
    global _last_request_time

    with _rate_limit_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.time()

    try:
        response = requests.get(url, params=params, timeout=10)
        return response
    except requests.RequestException:
        return None


def get_steam_review_score(appid):
    """Fetch review score for a Steam game.

    Returns a dict with:
    - review_score: percentage of positive reviews (0-100)
    - review_desc: text description (e.g., "Very Positive")
    - total_reviews: total number of reviews
    """
    url = f"https://store.steampowered.com/appreviews/{appid}"
    params = {
        "json": 1,
        "language": "all",
        "purchase_type": "all"
    }

    response = _rate_limited_request(url, params)
    if not response or response.status_code != 200:
        return None

    try:
        data = response.json()
        summary = data.get("query_summary", {})

        total_positive = summary.get("total_positive", 0)
        total_negative = summary.get("total_negative", 0)
        total_reviews = total_positive + total_negative

        if total_reviews == 0:
            return None

        review_score = round((total_positive / total_reviews) * 100, 1)
        review_desc = summary.get("review_score_desc", "")

        return {
            "review_score": review_score,
            "review_desc": review_desc,
            "total_reviews": total_reviews
        }
    except (ValueError, KeyError):
        return None


def _fetch_game_with_reviews(game_data):
    """Fetch a single game's review data and merge with game info."""
    appid = game_data.get("appid")

    result = {
        "name": game_data.get("name"),
        "appid": appid,
        "playtime_hours": round(game_data.get("playtime_forever", 0) / 60, 1),
        "icon_url": f"https://media.steampowered.com/steamcommunity/public/images/apps/{appid}/{game_data.get('img_icon_url')}.jpg"
    }

    # Fetch review score
    reviews = get_steam_review_score(appid)
    if reviews:
        result["review_score"] = reviews["review_score"]
        result["review_desc"] = reviews["review_desc"]
        result["total_reviews"] = reviews["total_reviews"]

    return result


def get_steam_library(fetch_reviews=True, max_workers=5):
    """Fetch games from Steam library using credentials from database.

    Args:
        fetch_reviews: Whether to fetch review scores (slower but more data)
        max_workers: Number of threads for parallel review fetching
    """
    creds = get_steam_credentials()
    STEAM_API_KEY = creds.get("api_key")
    STEAM_ID = creds.get("steam_id")

    if not STEAM_API_KEY or not STEAM_ID:
        print("Steam credentials not configured. Please set them in Settings.")
        return []

    url = f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
    params = {
        "key": STEAM_API_KEY,
        "steamid": STEAM_ID,
        "include_appinfo": True,
        "include_played_free_games": True
    }

    response = requests.get(url, params=params)
    data = response.json()

    raw_games = data.get("response", {}).get("games", [])

    if not fetch_reviews:
        # Fast path: just return basic info without reviews
        games = []
        for game in raw_games:
            games.append({
                "name": game.get("name"),
                "appid": game.get("appid"),
                "playtime_hours": round(game.get("playtime_forever", 0) / 60, 1),
                "icon_url": f"https://media.steampowered.com/steamcommunity/public/images/apps/{game['appid']}/{game.get('img_icon_url')}.jpg"
            })
        return games

    # Fetch reviews in parallel with threading
    games = []
    total = len(raw_games)
    completed = 0

    print(f"  Fetching review scores for {total} games ({max_workers} threads)...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_game = {
            executor.submit(_fetch_game_with_reviews, game): game
            for game in raw_games
        }

        for future in as_completed(future_to_game):
            try:
                result = future.result()
                games.append(result)
                completed += 1
                if completed % 50 == 0 or completed == total:
                    print(f"  Progress: {completed}/{total} games processed")
            except Exception as e:
                game = future_to_game[future]
                print(f"  Error fetching {game.get('name')}: {e}")
                # Still add the game without review data
                games.append({
                    "name": game.get("name"),
                    "appid": game.get("appid"),
                    "playtime_hours": round(game.get("playtime_forever", 0) / 60, 1),
                    "icon_url": f"https://media.steampowered.com/steamcommunity/public/images/apps/{game['appid']}/{game.get('img_icon_url')}.jpg"
                })

    return games


if __name__ == "__main__":
    import sys

    # Allow skipping reviews for quick testing
    fetch_reviews = "--no-reviews" not in sys.argv

    library = get_steam_library(fetch_reviews=fetch_reviews)
    with open("steam_library.json", "w") as f:
        json.dump(library, f, indent=2)

    # Show summary
    print(f"\nExported {len(library)} Steam games")
    if fetch_reviews:
        with_reviews = sum(1 for g in library if g.get("review_score"))
        print(f"Games with review scores: {with_reviews}")
