# battlenet.py
# Fetches owned games from Battle.net using session cookie authentication

import json
import sqlite3
import requests
from datetime import datetime

from ..services.settings import get_battlenet_credentials
from ..config import DATABASE_PATH

# Battle.net API endpoints (from Playnite implementation)
API_BASE = "https://account.battle.net/api"
GAMES_SUBS_ENDPOINT = f"{API_BASE}/games-and-subs"
CLASSIC_GAMES_ENDPOINT = f"{API_BASE}/classic-games"

# Required headers for API requests
REQUIRED_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def get_session():
    """Get authenticated requests session using stored cookie."""
    creds = get_battlenet_credentials()
    session_cookie = creds.get("session_cookie")

    if not session_cookie:
        return None

    session = requests.Session()
    session.headers.update(REQUIRED_HEADERS)

    # Set all the cookies that might be needed for authentication
    # The session_cookie field can contain multiple cookies separated by semicolons
    # Or it can be just the access_token value
    if ";" in session_cookie:
        # Multiple cookies provided
        for cookie_pair in session_cookie.split(";"):
            cookie_pair = cookie_pair.strip()
            if "=" in cookie_pair:
                name, value = cookie_pair.split("=", 1)
                session.cookies.set(name.strip(), value.strip(), domain=".battle.net")
    else:
        # Single value - try as access_token cookie
        session.cookies.set("access_token", session_cookie, domain=".battle.net")

    return session


def verify_session(session):
    """Verify the session is authenticated."""
    try:
        response = session.get(GAMES_SUBS_ENDPOINT)
        if response.status_code == 200:
            data = response.json()
            # If we get game data, we're authenticated
            if isinstance(data, dict) and "gameAccounts" in data:
                game_count = len(data.get("gameAccounts", []))
                print(f"Authenticated - found {game_count} game accounts")
                return True
            elif isinstance(data, list):
                print(f"Authenticated - found {len(data)} entries")
                return True
        print(f"Authentication failed: {response.status_code}")
        return False
    except Exception as e:
        print(f"Error verifying session: {e}")
        return False


def get_owned_games(session):
    """Fetch modern games from games-and-subs endpoint."""
    try:
        response = session.get(GAMES_SUBS_ENDPOINT)
        if response.status_code != 200:
            print(f"Failed to fetch games: {response.status_code}")
            return []

        data = response.json()
        games = []

        # Handle different response formats
        game_accounts = []
        if isinstance(data, dict):
            game_accounts = data.get("gameAccounts", [])
        elif isinstance(data, list):
            game_accounts = data

        for game in game_accounts:
            # Extract game info
            title_id = game.get("titleId")
            name = game.get("localizedGameName") or game.get("titleName") or game.get("gameAccountName")

            if not name:
                continue

            # Build cover image URL if available
            icon_filename = game.get("regionalGameFranchiseIconFilename") or game.get("gameIconFilename")
            cover_image = None
            if icon_filename:
                cover_image = f"https://blzmedia-a.akamaihd.net/account/static/local-common/images/game-icons/{icon_filename}"

            games.append({
                "title_id": str(title_id) if title_id else None,
                "name": name,
                "cover_image": cover_image,
                "region": game.get("region"),
                "game_account_status": game.get("gameAccountStatus"),
                "raw_data": game,
            })

        return games

    except Exception as e:
        print(f"Error fetching games: {e}")
        return []


def get_classic_games(session):
    """Fetch classic games from classic-games endpoint."""
    try:
        response = session.get(CLASSIC_GAMES_ENDPOINT)
        if response.status_code != 200:
            print(f"Failed to fetch classic games: {response.status_code}")
            return []

        data = response.json()
        games = []

        # Handle different response formats
        classic_games = []
        if isinstance(data, dict):
            classic_games = data.get("classicGames", [])
        elif isinstance(data, list):
            classic_games = data

        for game in classic_games:
            name = game.get("localizedGameName") or game.get("gameAccountName")

            if not name:
                continue

            # Classic games may not have title IDs, use name as identifier
            store_id = game.get("titleId")
            if not store_id:
                store_id = name.lower().replace(" ", "_").replace(":", "").replace("-", "_")

            # Build cover image URL if available
            icon_filename = game.get("regionalGameFranchiseIconFilename") or game.get("gameIconFilename")
            cover_image = None
            if icon_filename:
                cover_image = f"https://blzmedia-a.akamaihd.net/account/static/local-common/images/game-icons/{icon_filename}"

            games.append({
                "title_id": str(store_id),
                "name": name,
                "cover_image": cover_image,
                "is_classic": True,
                "raw_data": game,
            })

        return games

    except Exception as e:
        print(f"Error fetching classic games: {e}")
        return []


def get_battlenet_library():
    """Fetch all games from Battle.net library."""
    session = get_session()
    if not session:
        print("Error: Battle.net cookies not configured")
        print("\nTo set up Battle.net:")
        print("1. Log in to account.battle.net in your browser")
        print("2. Open Developer Tools (F12) -> Network tab")
        print("3. Refresh the page and click any request")
        print("4. Find 'Cookie' in Request Headers and copy the entire value")
        print("5. Paste it in the Settings page")
        return None

    if not verify_session(session):
        print("Error: Cookies are invalid or expired")
        print("Please update your Battle.net cookies in Settings")
        return None

    print("Fetching Battle.net library...")

    # Get modern games
    modern_games = get_owned_games(session)
    print(f"  Found {len(modern_games)} modern games")

    # Get classic games
    classic_games = get_classic_games(session)
    print(f"  Found {len(classic_games)} classic games")

    # Combine and deduplicate by title_id
    all_games = []
    seen_ids = set()

    for game in modern_games + classic_games:
        title_id = game.get("title_id")
        if title_id and title_id in seen_ids:
            continue
        if title_id:
            seen_ids.add(title_id)
        all_games.append(game)

    return all_games


def import_to_database(games):
    """Import Battle.net games to the database."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    count = 0
    for game in games:
        try:
            cursor.execute(
                """INSERT OR REPLACE INTO games (
                    name, store, store_id, cover_image,
                    extra_data, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    game.get("name"),
                    "battlenet",
                    game.get("title_id"),
                    game.get("cover_image"),
                    json.dumps(game.get("raw_data", {})),
                    datetime.now().isoformat(),
                ),
            )
            count += 1
        except Exception as e:
            print(f"Error importing {game.get('name')}: {e}")

    conn.commit()
    conn.close()

    return count


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Import Battle.net library")
    parser.add_argument("--export", type=str, help="Export to JSON file instead of database")
    args = parser.parse_args()

    print("Battle.net Library Import")
    print("=" * 60)

    games = get_battlenet_library()
    if not games:
        print("Failed to fetch Battle.net library")
        return

    print(f"\nFound {len(games)} unique games")

    if not games:
        return

    if args.export:
        # Export to JSON
        with open(args.export, "w") as f:
            json.dump(games, f, indent=2)
        print(f"Exported to {args.export}")
    else:
        # Import to database
        count = import_to_database(games)
        print(f"Imported {count} games to database")


if __name__ == "__main__":
    main()
