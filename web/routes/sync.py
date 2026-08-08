import json
import re
import sqlite3
import os
from datetime import datetime
from enum import Enum
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import DATABASE_PATH
from ..services.jobs import (
    JobType, create_job, update_job_progress, complete_job, fail_job, run_job_async
)
from ..services.notifications import notify_new_games

router = APIRouter(tags=["Sync"])


def sync_ggdeals_collection(conn, progress_callback=None):
    """Export pending owned games if the optional GG.deals token is configured."""
    from ..services.ggdeals_sync import is_configured, sync_pending_games

    if not is_configured():
        return None
    try:
        return sync_pending_games(conn, progress_callback=progress_callback)
    except Exception as e:
        # Importing stores remains successful when GG.deals is temporarily
        # unavailable; pending games will be retried during the next sync.
        print(f"GG.deals sync error: {e}")
        return {"error": str(e)}


def run_store_import(conn, results, key, display_name, importer):
  result = importer(conn)

  results[key] = result["count"]
  notify_new_games(display_name, result["added_games"])

  return result
class StoreType(str, Enum):
    steam = "steam"
    epic = "epic"
    gog = "gog"
    itch = "itch"
    humble = "humble"
    battlenet = "battlenet"
    amazon = "amazon"
    ea = "ea"
    xbox = "xbox"
    ubisoft = "ubisoft"
    local = "local"
    all = "all"


@router.post("/api/sync/store/{store}")
def sync_store(store: StoreType):
    """Sync games from a store."""
    from ..services.database_builder import (
        create_database, import_steam_games, import_epic_games,
        import_gog_games, import_itch_games, import_humble_games,
        import_battlenet_games, import_amazon_games, import_ea_games,
        import_xbox_games, import_local_games
    )

    try:
        create_database()
        conn = sqlite3.connect(DATABASE_PATH)

        store_map = {
            StoreType.steam: ("steam", "Steam", import_steam_games),
            StoreType.epic: ("epic", "Epic", import_epic_games),
            StoreType.gog: ("gog", "GOG", import_gog_games),
            StoreType.itch: ("itch", "Itch.io", import_itch_games),
            StoreType.humble: ("humble", "Humble", import_humble_games),
            StoreType.battlenet: ("battlenet", "Battle.net", import_battlenet_games),
            StoreType.amazon: ("amazon", "Amazon", import_amazon_games),
            StoreType.ea: ("ea", "EA", import_ea_games),
            StoreType.xbox: ("xbox", "Xbox", import_xbox_games),
            StoreType.local: ("local", "Local", import_local_games),
        }

        results = {}

        if store == StoreType.all:
            for s_type, (key, display_name, importer) in store_map.items():
                run_store_import(conn, results, key, display_name, importer)
        elif store in store_map:
            key, display_name, importer = store_map[store]
            run_store_import(conn, results, key, display_name, importer)

        ggdeals_result = sync_ggdeals_collection(conn)
        if ggdeals_result is not None:
            results["ggdeals"] = ggdeals_result

        conn.close()

        if store == StoreType.all:
            total = sum(v for v in results.values() if isinstance(v, int))
            message = f"Synced {total} games: " + ", ".join(
                f"{s.capitalize()}: {c}" for s, c in results.items()
            )
        else:
            count = results.get(store.value, 0)
            message = f"Synced {count} games from {store.value.capitalize()}"

        return {"success": True, "message": message, "results": results}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/sync/igdb/{mode}")
def sync_igdb(mode: str):
    """Sync IGDB metadata. Mode can be 'new'/'missing' (unmatched only) or 'all' (resync everything)."""
    from ..services.igdb_sync import IGDBClient, sync_games as igdb_sync_games, add_igdb_columns

    try:
        conn = sqlite3.connect(DATABASE_PATH)
        add_igdb_columns(conn)

        client = IGDBClient()
        force = (mode == "all")
        matched, failed = igdb_sync_games(conn, client, force=force)

        conn.close()

        message = f"IGDB sync complete: {matched} matched, {failed} failed/no match"
        return {"success": True, "message": message, "matched": matched, "failed": failed}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/sync/metacritic/{mode}")
def sync_metacritic(mode: str):
    """Sync Metacritic scores. Mode can be 'missing' (unmatched only) or 'all' (resync everything)."""
    from ..services.metacritic_sync import (
        MetacriticClient, sync_games as metacritic_sync_games, add_metacritic_columns
    )

    try:
        conn = sqlite3.connect(DATABASE_PATH)
        add_metacritic_columns(conn)

        client = MetacriticClient()
        force = (mode == "all")
        matched, failed = metacritic_sync_games(conn, client, force=force)

        conn.close()

        message = f"Metacritic sync complete: {matched} matched, {failed} failed/no match"
        return {"success": True, "message": message, "matched": matched, "failed": failed}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/sync/steamgriddb/{mode}")
def sync_steamgriddb(mode: str):
    """Sync SteamGridDB covers. Mode can be 'missing' or 'all'."""
    from ..services.steamgriddb_sync import (
        SteamGridDBClient,
        sync_games as steamgriddb_sync_games,
        add_steamgriddb_columns,
    )

    try:
        conn = sqlite3.connect(DATABASE_PATH)
        add_steamgriddb_columns(conn)

        client = SteamGridDBClient(os.getenv("STEAMGRIDDB_API_KEY"))
        force = (mode == "all")
        matched, failed = steamgriddb_sync_games(conn, client, force=force)

        conn.close()

        message = f"SteamGridDB sync complete: {matched} matched, {failed} failed/no match"
        return {"success": True, "message": message, "matched": matched, "failed": failed}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# =============================================================================
# Async Job-based Sync Endpoints
# =============================================================================

@router.post("/api/sync/store/{store}/async")
def sync_store_async(store: StoreType):
    """Start a background job to sync games from a store. Returns job ID for tracking."""
    from ..services.database_builder import (
        create_database, import_steam_games, import_epic_games,
        import_gog_games, import_itch_games, import_humble_games,
        import_battlenet_games, import_amazon_games, import_ea_games,
        import_xbox_games, import_local_games
    )

    store_name = "all stores" if store == StoreType.all else store.value.capitalize()
    job_id = create_job(JobType.STORE_SYNC, f"Starting {store_name} sync...")

    def run_sync(job_id: str):
        try:
            create_database()
            conn = sqlite3.connect(DATABASE_PATH)

            store_map = {
                StoreType.steam: ("steam", "Steam", import_steam_games),
                StoreType.epic: ("epic", "Epic", import_epic_games),
                StoreType.gog: ("gog", "GOG", import_gog_games),
                StoreType.itch: ("itch", "Itch.io", import_itch_games),
                StoreType.humble: ("humble", "Humble", import_humble_games),
                StoreType.battlenet: ("battlenet", "Battle.net", import_battlenet_games),
                StoreType.amazon: ("amazon", "Amazon", import_amazon_games),
                StoreType.ea: ("ea", "EA", import_ea_games),
                StoreType.xbox: ("xbox", "Xbox", import_xbox_games),
                StoreType.local: ("local", "Local", import_local_games),
            }

            if store == StoreType.all:
                stores_to_sync = list(store_map.values())
            else:
                stores_to_sync = [store_map[store]] if store in store_map else []

            total = len(stores_to_sync)
            results = {}

            for i, (key, display_name, importer) in enumerate(stores_to_sync, 1):
                update_job_progress(job_id, i, total, f"Syncing {display_name}...")
                try:
                    run_store_import(conn, results, key, display_name, importer)
                except Exception as e:
                    results[key] = f"Error: {str(e)}"

            ggdeals_result = sync_ggdeals_collection(
                conn,
                progress_callback=lambda current, total, message: update_job_progress(
                    job_id, current, total, message
                ),
            )
            if ggdeals_result is not None:
                results["ggdeals"] = ggdeals_result

            conn.close()

            if store == StoreType.all:
                total_games = sum(v for v in results.values() if isinstance(v, int))
                message = f"Synced {total_games} games: " + ", ".join(
                    f"{s.capitalize()}: {c}" for s, c in results.items()
                )
            else:
                count = results.get(store.value, 0)
                message = f"Synced {count} games from {store.value.capitalize()}"

            complete_job(job_id, json.dumps(results), message)

        except Exception as e:
            fail_job(job_id, str(e))

    run_job_async(job_id, run_sync)

    return {"success": True, "job_id": job_id, "message": f"Started {store_name} sync job"}


@router.post("/api/sync/igdb/{mode}/async")
def sync_igdb_async(mode: str):
    """Start a background job to sync IGDB metadata. Returns job ID for tracking."""
    from ..services.igdb_sync import IGDBClient, sync_games as igdb_sync_games, add_igdb_columns

    mode_text = "all games" if mode == "all" else "missing metadata"
    job_id = create_job(JobType.IGDB_SYNC, f"Starting IGDB sync ({mode_text})...")

    def run_sync(job_id: str):
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row

            add_igdb_columns(conn)
            update_job_progress(job_id, 0, 1, "Initializing IGDB sync...")

            def on_progress(current, total, message):
                update_job_progress(job_id, current, total, message)

            client = IGDBClient()
            force = (mode == "all")
            matched, failed = igdb_sync_games(conn, client, force=force, progress_callback=on_progress)

            conn.close()

            message = f"IGDB sync complete: {matched} matched, {failed} failed/no match"
            complete_job(job_id, json.dumps({"matched": matched, "failed": failed}), message)

        except Exception as e:
            fail_job(job_id, str(e))

    run_job_async(job_id, run_sync)

    return {"success": True, "job_id": job_id, "message": f"Started IGDB sync job ({mode_text})"}


@router.post("/api/sync/metacritic/{mode}/async")
def sync_metacritic_async(mode: str):
    """Start a background job to sync Metacritic scores. Returns job ID for tracking."""
    from ..services.metacritic_sync import (
        MetacriticClient, sync_games as metacritic_sync_games, add_metacritic_columns
    )

    mode_text = "all games" if mode == "all" else "missing scores"
    job_id = create_job(JobType.METACRITIC_SYNC, f"Starting Metacritic sync ({mode_text})...")

    def run_sync(job_id: str):
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row

            add_metacritic_columns(conn)
            update_job_progress(job_id, 0, 1, "Initializing Metacritic sync...")

            def on_progress(current, total, message):
                update_job_progress(job_id, current, total, message)

            client = MetacriticClient()
            force = (mode == "all")
            matched, failed = metacritic_sync_games(conn, client, force=force, progress_callback=on_progress)

            conn.close()

            message = f"Metacritic sync complete: {matched} matched, {failed} failed/no match"
            complete_job(job_id, json.dumps({"matched": matched, "failed": failed}), message)

        except Exception as e:
            fail_job(job_id, str(e))

    run_job_async(job_id, run_sync)

    return {"success": True, "job_id": job_id, "message": f"Started Metacritic sync job ({mode_text})"}


@router.post("/api/sync/steamgriddb/{mode}/async")
def sync_steamgriddb_async(mode: str):
    """Start a background job to sync SteamGridDB covers. Returns job ID for tracking."""
    from ..services.steamgriddb_sync import (
        SteamGridDBClient,
        sync_games as steamgriddb_sync_games,
        add_steamgriddb_columns,
    )

    mode_text = "all games" if mode == "all" else "missing covers"
    job_id = create_job(JobType.STEAMGRIDDB_SYNC, f"Starting SteamGridDB sync ({mode_text})...")

    def run_sync(job_id: str):
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row

            add_steamgriddb_columns(conn)
            update_job_progress(job_id, 0, 1, "Initializing SteamGridDB sync...")

            def on_progress(current, total, message):
                update_job_progress(job_id, current, total, message)

            client = SteamGridDBClient(os.getenv("STEAMGRIDDB_API_KEY"))
            force = (mode == "all")

            matched, failed = steamgriddb_sync_games(
                conn, client, force=force, progress_callback=on_progress
            )

            conn.close()

            message = f"SteamGridDB sync complete: {matched} matched, {failed} failed/no match"
            complete_job(job_id, json.dumps({"matched": matched, "failed": failed}), message)

        except Exception as e:
            fail_job(job_id, str(e))

    run_job_async(job_id, run_sync)

    return {"success": True, "job_id": job_id, "message": f"Started SteamGridDB sync job ({mode_text})"}


@router.post("/api/sync/protondb/{mode}")
def sync_protondb(mode: str):
    """Sync ProtonDB data. Mode can be 'missing' (unmatched only) or 'all' (resync everything)."""
    from ..services.protondb_sync import (
        ProtonDBClient, sync_games as protondb_sync_games, add_protondb_columns
    )

    try:
        conn = sqlite3.connect(DATABASE_PATH)
        add_protondb_columns(conn)

        client = ProtonDBClient()
        force = (mode == "all")
        matched, failed = protondb_sync_games(conn, client, force=force)

        conn.close()

        message = f"ProtonDB sync complete: {matched} matched, {failed} failed/no data"
        return {"success": True, "message": message, "matched": matched, "failed": failed}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/sync/protondb/{mode}/async")
def sync_protondb_async(mode: str):
    """Start a background job to sync ProtonDB data. Returns job ID for tracking."""
    from ..services.protondb_sync import (
        ProtonDBClient, sync_games as protondb_sync_games, add_protondb_columns
    )

    mode_text = "all Steam games" if mode == "all" else "missing data"
    job_id = create_job(JobType.PROTONDB_SYNC, f"Starting ProtonDB sync ({mode_text})...")

    def run_sync(job_id: str):
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row

            add_protondb_columns(conn)
            update_job_progress(job_id, 0, 1, "Initializing ProtonDB sync...")

            def on_progress(current, total, message):
                update_job_progress(job_id, current, total, message)

            client = ProtonDBClient()
            force = (mode == "all")
            matched, failed = protondb_sync_games(conn, client, force=force, progress_callback=on_progress)

            conn.close()

            message = f"ProtonDB sync complete: {matched} matched, {failed} failed/no data"
            complete_job(job_id, json.dumps({"matched": matched, "failed": failed}), message)

        except Exception as e:
            fail_job(job_id, str(e))

    run_job_async(job_id, run_sync)

    return {"success": True, "job_id": job_id, "message": f"Started ProtonDB sync job ({mode_text})"}


@router.post("/api/sync/ggdeals/async")
def sync_ggdeals_async():
    """Export pending owned games to the GG.deals collection."""
    from ..services.ggdeals_sync import is_configured, sync_pending_games

    if not is_configured():
        raise HTTPException(status_code=400, detail="GG.deals token is not configured")

    job_id = create_job(JobType.GGDEALS_SYNC, "Starting GG.deals collection sync...")

    def run_sync(job_id: str):
        conn = None
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            result = sync_pending_games(
                conn,
                progress_callback=lambda current, total, message: update_job_progress(
                    job_id, current, total, message
                ),
            )
            message = (
                f"GG.deals sync complete: {result['added']} added, "
                f"{result['skipped']} already owned, {result['miss']} not matched"
            )
            complete_job(job_id, json.dumps(result), message)
        except Exception as e:
            fail_job(job_id, str(e))
        finally:
            if conn is not None:
                conn.close()

    run_job_async(job_id, run_sync)
    return {"success": True, "job_id": job_id, "message": "Started GG.deals collection sync"}


class UbisoftGame(BaseModel):
    title: str
    playtime: Optional[str] = None
    lastPlayed: Optional[str] = None
    platform: Optional[str] = None


class UbisoftImportRequest(BaseModel):
    games: List[UbisoftGame]


class GOGGame(BaseModel):
    id: str
    title: str
    profileUrl: Optional[str] = None
    storeUrl: Optional[str] = None


class GOGImportRequest(BaseModel):
    games: List[GOGGame]


@router.post("/api/import/ubisoft")
def import_ubisoft_games(request: UbisoftImportRequest):
    """Import games scraped from Ubisoft account page."""
    from ..services.database_builder import create_database

    try:
        create_database()
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()

        count = 0
        for game in request.games:
            try:
                playtime_hours = None
                if game.playtime:
                    hours_match = re.search(r'(\d+)\s*hour', game.playtime)
                    mins_match = re.search(r'(\d+)\s*min', game.playtime)
                    hours = int(hours_match.group(1)) if hours_match else 0
                    mins = int(mins_match.group(1)) if mins_match else 0
                    playtime_hours = hours + (mins / 60) if (hours or mins) else None

                store_id = game.title.lower().replace(' ', '-').replace(':', '').replace("'", "")

                extra_data = {
                    "playtime_raw": game.playtime,
                    "last_played": game.lastPlayed,
                    "platform": game.platform
                }

                cursor.execute("""
                    INSERT INTO games (
                        name, store, store_id, playtime_hours, extra_data, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store, store_id) DO UPDATE SET
                        name = excluded.name,
                        playtime_hours = excluded.playtime_hours,
                        extra_data = excluded.extra_data,
                        updated_at = excluded.updated_at
                """, (
                    game.title,
                    "ubisoft",
                    store_id,
                    playtime_hours,
                    json.dumps(extra_data),
                    datetime.now().isoformat()
                ))
                count += 1
            except Exception as e:
                print(f"  Error importing {game.title}: {e}")

        conn.commit()
        conn.close()

        return {
            "success": True,
            "message": f"Imported {count} Ubisoft games",
            "count": count
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/import/gog")
def import_gog_games(request: GOGImportRequest):
    """Import games scraped from GOG library page."""
    from ..services.database_builder import create_database

    try:
        create_database()
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()

        count = 0
        for game in request.games:
            try:
                extra_data = {
                    "profile_url": game.profileUrl,
                    "store_url": game.storeUrl
                }

                cursor.execute("""
                    INSERT INTO games (
                        name, store, store_id, extra_data, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(store, store_id) DO UPDATE SET
                        name = excluded.name,
                        extra_data = excluded.extra_data,
                        updated_at = excluded.updated_at
                """, (
                    game.title,
                    "gog",
                    game.id,
                    json.dumps(extra_data),
                    datetime.now().isoformat()
                ))
                count += 1
            except Exception as e:
                print(f"  Error importing {game.title}: {e}")

        conn.commit()
        conn.close()

        return {
            "success": True,
            "message": f"Imported {count} GOG games",
            "count": count
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
