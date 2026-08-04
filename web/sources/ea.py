# ea.py
# Fetches owned games from EA using bearer token authentication
# Based on: https://github.com/Jeshibu/PlayniteExtensions
# User obtains bearer token via JavaScript snippet in browser console

import json
import requests

from ..services.settings import get_ea_credentials

# EA API endpoints
GRAPHQL_ENDPOINT = "https://service-aggregation-layer.juno.ea.com/graphql"

# Required headers for API requests
REQUIRED_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def get_bearer_token():
    """Get stored bearer token from settings."""
    creds = get_ea_credentials()
    token = creds.get("bearer_token")

    if token:
        # Strip "Bearer " prefix if user accidentally included it
        token = token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()

    return token if token else None


def get_owned_games(token):
    """Query GraphQL endpoint for owned games using Jeshibu's persisted query approach."""
    try:
        session = requests.Session()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        all_games = []
        next_offset = None
        limit = 200

        # Persisted query hash from Jeshibu's PlayniteExtensions
        QUERY_HASH = "5de4178ee7e1f084ce9deca856c74a9e03547a67dfafc0cb844d532fb54ae73d"

        while True:
            # Build variables - try without ownershipMethods to get all games
            variables = {
                "isMac": False,
                "addFieldsToPreloadGames": True,
                "locale": "en-US",
                "limit": limit,
                "type": ["DIGITAL_FULL_GAME", "PACKAGED_FULL_GAME"],
                "entitlementEnabled": True,
                "storefronts": ["EA"],
                "platforms": ["PC"]
            }

            if next_offset:
                variables["next"] = next_offset

            # Use persisted query extension
            payload = {
                "operationName": "getPreloadedOwnedGames",
                "variables": variables,
                "extensions": {
                    "persistedQuery": {
                        "version": 1,
                        "sha256Hash": QUERY_HASH
                    }
                }
            }

            response = session.post(
                GRAPHQL_ENDPOINT,
                headers=headers,
                json=payload
            )

            print(f"  GraphQL response status: {response.status_code}")

            if response.status_code == 401:
                print("  Token expired or invalid - please get a new token")
                break

            if response.status_code != 200:
                print(f"  GraphQL error response: {response.text[:500]}")
                break

            try:
                data = response.json()
            except json.JSONDecodeError:
                print(f"  GraphQL response not JSON: {response.text[:200]}")
                break

            if "errors" in data:
                print(f"  GraphQL errors: {data['errors']}")
                break

            # Debug: print the response structure
            print(f"  Response keys: {list(data.keys())}")
            if "data" in data:
                print(f"  data keys: {list(data['data'].keys()) if data['data'] else 'None'}")
                if data.get("data", {}).get("me"):
                    print(f"  me keys: {list(data['data']['me'].keys())}")

            # Navigate the response structure
            me = data.get("data", {}).get("me", {})
            preloaded = me.get("preloadedOwnedGames", {})
            items = preloaded.get("items", [])

            # If preloadedOwnedGames is empty, try other possible keys
            if not items:
                for key in me.keys():
                    val = me.get(key)
                    if isinstance(val, dict) and "items" in val:
                        items = val.get("items", [])
                        print(f"  Found items under 'me.{key}'")
                        break
                    elif isinstance(val, list):
                        items = val
                        print(f"  Found list under 'me.{key}'")
                        break

            print(f"  Got {len(items)} items")

            if not items:
                break

            # Debug: print first item structure
            if items and len(all_games) == 0:
                print(f"  First item keys: {list(items[0].keys()) if isinstance(items[0], dict) else type(items[0])}")
                print(f"  First item sample: {json.dumps(items[0], indent=2)[:1000]}")

            for item in items:
                # Parse based on actual EA response structure:
                # item.originOfferId, item.product.name, item.product.gameSlug, etc.
                product = item.get("product", {})
                base_item = product.get("baseItem", {})

                # Get name from product or baseItem
                name = product.get("name") or base_item.get("title")
                if not name:
                    continue

                # Get offer ID
                offer_id = item.get("originOfferId") or item.get("id")

                # Get game slug for potential cover image URL
                game_slug = product.get("gameSlug") or base_item.get("baseGameSlug")

                # Build cover image URL from game slug if available
                # EA uses URLs like: https://media.contentapi.ea.com/content/dam/ea/...
                cover_image = None
                if game_slug:
                    # Try common EA image URL patterns
                    cover_image = f"https://media.contentapi.ea.com/content/dam/eacom/en-us/common/games/{game_slug}/packart.jpg"

                # Get release date from lifecycle status
                release_date = None
                lifecycle = product.get("lifecycleStatus", [])
                if lifecycle:
                    release_date = lifecycle[0].get("playableStartDate")

                # Get game type
                game_type = base_item.get("gameType")

                all_games.append({
                    "offer_id": str(offer_id) if offer_id else None,
                    "name": name,
                    "cover_image": cover_image,
                    "offer_type": game_type,
                    "release_date": release_date,
                    "game_slug": game_slug,
                    "raw_data": item,
                })

            # Check for pagination
            next_offset = preloaded.get("next")
            if not next_offset or len(items) < limit:
                break

        return all_games

    except Exception as e:
        print(f"Error fetching games: {e}")
        import traceback
        traceback.print_exc()
        return []


def get_ea_library():
    """Fetch all games from EA library."""
    token = get_bearer_token()
    if not token:
        print("Error: EA bearer token not configured")
        print("\nTo set up EA, use the JavaScript snippet in Settings to get your token")
        return None

    print("Fetching EA library...")

    games = get_owned_games(token)
    print(f"  Found {len(games)} EA games")

    # Deduplicate by offer_id
    seen_ids = set()
    unique_games = []
    for game in games:
        offer_id = game.get("offer_id")
        if offer_id and offer_id in seen_ids:
            continue
        if offer_id:
            seen_ids.add(offer_id)
        unique_games.append(game)

    return unique_games


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Import EA library")
    parser.add_argument("--token", type=str, help="EA bearer token (for testing)")
    parser.add_argument("--export", type=str, help="Export to JSON file instead of database")
    args = parser.parse_args()

    print("EA Library Import")
    print("=" * 60)

    if args.token:
        # Use provided token for testing
        print("Using provided token...")
        games = get_owned_games(args.token)
    else:
        games = get_ea_library()

    if not games:
        print("Failed to fetch EA library")
        return

    print(f"\nFound {len(games)} unique games")

    if args.export:
        with open(args.export, "w") as f:
            json.dump(games, f, indent=2)
        print(f"Exported to {args.export}")
    else:
        for game in games[:10]:
            print(f"  - {game['name']}")
        if len(games) > 10:
            print(f"  ... and {len(games) - 10} more")


if __name__ == "__main__":
    main()
