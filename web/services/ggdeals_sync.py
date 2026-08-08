"""Synchronize owned games to a GG.deals collection.

This uses the collection-import contract exposed for the GG.deals Playnite
integration.  It only adds games: it never removes entries from GG.deals.
"""

import json
import sqlite3
import uuid
from datetime import datetime

import requests

from ..utils.helpers import get_store_url
from .settings import GGDEALS_TOKEN, get_setting


GGDEALS_IMPORT_URL = "https://api.gg.deals/playnite/collection/import/"
REQUEST_TIMEOUT = 180
BATCH_SIZE = 10
_GAME_UUID_NAMESPACE = uuid.UUID("7d20ea35-07c8-4466-b5dc-714a1573f4b8")

# Values are the launcher names accepted by GG.deals' Playnite import API.
STORE_LAUNCHERS = {
    "steam": "steam",
    "epic": "epic",
    "gog": "gog",
    "itch": "itch",
    "ea": "ea",
    "ubisoft": "ubisoft",
    "battlenet": "battle-net",
    "amazon": "prime-gaming",
    "xbox": "microsoft",
    "local": "other",
    "humble": "other",
}


def add_ggdeals_columns(conn: sqlite3.Connection) -> None:
    """Add GG.deals bookkeeping columns to existing databases."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(games)")
    columns = {row[1] for row in cursor.fetchall()}

    additions = {
        "ggdeals_status": "TEXT",
        "ggdeals_message": "TEXT",
        "ggdeals_url": "TEXT",
        "ggdeals_synced_at": "TIMESTAMP",
    }
    for column, column_type in additions.items():
        if column not in columns:
            cursor.execute(f"ALTER TABLE games ADD COLUMN {column} {column_type}")
    conn.commit()


def is_configured() -> bool:
    return bool(get_setting(GGDEALS_TOKEN, "").strip())


def _release_fields(value):
    """Return the optional release fields understood by GG.deals."""
    if not value:
        return {}
    value = str(value)
    year = value[:4] if len(value) >= 4 and value[:4].isdigit() else None
    fields = {"ReleaseDate": value[:10]}
    if year:
        fields["ReleaseYear"] = int(year)
    return fields


def _safe_store_link(game: dict) -> list[dict]:
    """Create a public store link without exporting account-specific URLs."""
    store = game["store"]
    # These URLs are either account pages, generic library pages, or can carry
    # a Humble download key. They do not improve matching enough to export.
    if store in {"amazon", "battlenet", "humble", "local"}:
        return []

    url = get_store_url(store, game.get("store_id"), game.get("extra_data"))
    if not url or "?key=" in url:
        return []
    return [{"Name": store.capitalize(), "Url": url}]


def _game_payload(game: dict) -> tuple[str, dict]:
    """Convert a Backlogia row into GG.deals' Playnite-compatible shape."""
    game_uuid = str(uuid.uuid5(_GAME_UUID_NAMESPACE, f"backlogia-game:{game['id']}"))
    payload = {
        "gg_launcher": STORE_LAUNCHERS.get(game["store"], "other"),
        "Id": game_uuid,
        "GameId": str(game.get("store_id") or game["id"]),
        "Links": _safe_store_link(game),
        "Source": {"Name": game["store"].capitalize()},
        "Name": game["name"],
    }
    payload.update(_release_fields(game.get("release_date")))
    return game_uuid, payload


def _pending_games(conn: sqlite3.Connection, force: bool) -> list[dict]:
    cursor = conn.cursor()
    # Streaming-only Game Pass entries are not owned games and must not be
    # added to the user's GG.deals collection.
    status_filter = "1 = 1" if force else "(ggdeals_status IS NULL OR ggdeals_status = 'error')"
    cursor.execute(
        f"""
        SELECT * FROM games
        WHERE {status_filter}
          AND (extra_data IS NULL OR json_extract(extra_data, '$.is_streaming') IS NOT 1)
        ORDER BY id
        """
    )
    return [dict(row) for row in cursor.fetchall()]


def _store_results(conn: sqlite3.Connection, games_by_uuid: dict[str, int], results: list[dict]) -> dict:
    cursor = conn.cursor()
    counts = {"added": 0, "skipped": 0, "miss": 0, "ignored": 0, "error": 0}
    now = datetime.now().isoformat()

    for result in results:
        game_id = games_by_uuid.get(str(result.get("id", result.get("Id", ""))).lower())
        if game_id is None:
            continue

        status = str(result.get("status", result.get("Status", "Error"))).lower()
        status = status if status in counts else "error"
        counts[status] += 1
        cursor.execute(
            """
            UPDATE games
            SET ggdeals_status = ?, ggdeals_message = ?, ggdeals_url = ?,
                ggdeals_synced_at = ?
            WHERE id = ?
            """,
            (
                status,
                result.get("message", result.get("Message")),
                result.get("url", result.get("Url")),
                now,
                game_id,
            ),
        )

    conn.commit()
    return counts


def sync_pending_games(conn: sqlite3.Connection, force: bool = False, progress_callback=None) -> dict:
    """Export unsynchronized owned games and persist GG.deals' per-game result."""
    token = get_setting(GGDEALS_TOKEN, "").strip()
    if not token:
        raise ValueError("GG.deals token is not configured")

    conn.row_factory = sqlite3.Row
    add_ggdeals_columns(conn)
    games = _pending_games(conn, force)
    if not games:
        return {"processed": 0, "added": 0, "skipped": 0, "miss": 0, "ignored": 0, "error": 0}

    print(f"[GG.deals] Found {len(games)} games to synchronize")

    totals = {"processed": len(games), "added": 0, "skipped": 0, "miss": 0, "ignored": 0, "error": 0}
    for offset in range(0, len(games), BATCH_SIZE):
        batch = games[offset:offset + BATCH_SIZE]
        games_by_uuid = {}
        payloads = []
        for game in batch:
            game_uuid, payload = _game_payload(game)
            games_by_uuid[game_uuid.lower()] = game["id"]
            payloads.append(payload)

        print(
            f"[GG.deals] Sending batch "
            f"{offset // BATCH_SIZE + 1}/"
            f"{(len(games) - 1) // BATCH_SIZE + 1} "
            f"({len(batch)} games)"
        )

        for attempt in range(1, 4):
            try:
                response = requests.post(
                    GGDEALS_IMPORT_URL,
                    json={
                        "version": "v1",
                        "token": token,
                        "data": json.dumps(payloads, ensure_ascii=False),
                    },
                    timeout=REQUEST_TIMEOUT,
                )
        
                print(f"[GG.deals] HTTP {response.status_code}")
                break
        
            except requests.Timeout:
                print(f"[GG.deals] Request timed out (attempt {attempt}/3)")
        
                if attempt == 3:
                    raise

        if response.status_code == 401:
            raise ValueError("GG.deals rejected the configured token")
        response.raise_for_status()

        body = response.json()
        if not body.get("success", body.get("Success")):
            data = body.get("data", body.get("Data", {}))
            message = data.get("message", data.get("Message", "GG.deals import failed"))
            raise RuntimeError(message)

        data = body.get("data", body.get("Data", {}))
        counts = _store_results(conn, games_by_uuid, data.get("result", data.get("Result", [])))
        for key, value in counts.items():
            totals[key] += value
        if progress_callback:
            progress_callback(min(offset + len(batch), len(games)), len(games), "Syncing GG.deals collection...")

    return totals
