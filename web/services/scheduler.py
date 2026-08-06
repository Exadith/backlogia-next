# services/scheduler.py
# Simple background scheduler for automatic store/metadata syncing.
# No external dependencies - runs a daemon thread that wakes up on an interval
# and triggers the same sync jobs as the manual "Sync" buttons in Settings.

import json
import os
import sqlite3
import threading
import time

from ..config import DATABASE_PATH
from .jobs import (
    JobType, create_job, update_job_progress, complete_job, fail_job,
    run_job_async, get_active_jobs
)

AUTO_SYNC_ENABLED = os.environ.get("AUTO_SYNC_ENABLED", "false").lower() == "true"
AUTO_SYNC_INTERVAL_HOURS = float(os.environ.get("AUTO_SYNC_INTERVAL_HOURS", "6"))
AUTO_SYNC_STORE = os.environ.get("AUTO_SYNC_STORE", "all")  # steam/epic/.../all
AUTO_SYNC_METADATA = os.environ.get("AUTO_SYNC_METADATA", "false").lower() == "true"

_STORE_SYNC_MAP = None


def _get_store_sync_map():
    """Lazy import to avoid circular imports at module load time."""
    global _STORE_SYNC_MAP
    if _STORE_SYNC_MAP is None:
        from .database_builder import (
            import_steam_games, import_epic_games, import_gog_games,
            import_itch_games, import_humble_games, import_battlenet_games,
            import_amazon_games, import_ea_games, import_xbox_games,
            import_local_games
        )
        _STORE_SYNC_MAP = {
            "steam": import_steam_games,
            "epic": import_epic_games,
            "gog": import_gog_games,
            "itch": import_itch_games,
            "humble": import_humble_games,
            "battlenet": import_battlenet_games,
            "amazon": import_amazon_games,
            "ea": import_ea_games,
            "xbox": import_xbox_games,
            "local": import_local_games,
        }
    return _STORE_SYNC_MAP


def _has_active_job(job_type_value):
    return any(j["type"] == job_type_value for j in get_active_jobs())


def _run_store_sync():
    if _has_active_job(JobType.STORE_SYNC.value):
        print("[scheduler] Skipping store sync - one is already running")
        return

    from .database_builder import create_database

    store_label = "all stores" if AUTO_SYNC_STORE == "all" else AUTO_SYNC_STORE
    job_id = create_job(JobType.STORE_SYNC, f"[Auto-sync] Starting {store_label} sync...")

    def run(job_id):
        try:
            create_database()
            conn = sqlite3.connect(DATABASE_PATH)
            sync_map = _get_store_sync_map()

            if AUTO_SYNC_STORE == "all":
                targets = list(sync_map.items())
            elif AUTO_SYNC_STORE in sync_map:
                targets = [(AUTO_SYNC_STORE, sync_map[AUTO_SYNC_STORE])]
            else:
                print(f"[scheduler] Unknown AUTO_SYNC_STORE '{AUTO_SYNC_STORE}', skipping")
                targets = []

            results = {}
            total = len(targets)
            for i, (name, func) in enumerate(targets, 1):
                update_job_progress(job_id, i, total, f"Syncing {name.capitalize()}...")
                try:
                    results[name] = func(conn)
                except Exception as e:
                    results[name] = f"Error: {e}"

            conn.close()

            total_games = sum(v for v in results.values() if isinstance(v, int))
            message = f"[Auto-sync] Synced {total_games} games: " + ", ".join(
                f"{s.capitalize()}: {c}" for s, c in results.items()
            )
            complete_job(job_id, json.dumps(results), message)
        except Exception as e:
            fail_job(job_id, str(e))

    run_job_async(job_id, run)


def _run_igdb_sync():
    if _has_active_job(JobType.IGDB_SYNC.value):
        print("[scheduler] Skipping IGDB sync - one is already running")
        return

    from .igdb_sync import IGDBClient, sync_games as igdb_sync_games, add_igdb_columns

    job_id = create_job(JobType.IGDB_SYNC, "[Auto-sync] Starting IGDB sync (missing)...")

    def run(job_id):
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row
            add_igdb_columns(conn)
            client = IGDBClient()
            matched, failed = igdb_sync_games(
                conn, client, force=False,
                progress_callback=lambda c, t, m: update_job_progress(job_id, c, t, m)
            )
            conn.close()
            complete_job(
                job_id, json.dumps({"matched": matched, "failed": failed}),
                f"[Auto-sync] IGDB sync complete: {matched} matched, {failed} failed"
            )
        except Exception as e:
            fail_job(job_id, str(e))

    run_job_async(job_id, run)


def _loop():
    interval_seconds = AUTO_SYNC_INTERVAL_HOURS * 3600
    print(
        f"[scheduler] Auto-sync enabled: every {AUTO_SYNC_INTERVAL_HOURS}h, "
        f"store={AUTO_SYNC_STORE}, metadata={AUTO_SYNC_METADATA}"
    )

    # Let the app finish starting up before the first run
    time.sleep(30)

    while True:
        try:
            _run_store_sync()
            if AUTO_SYNC_METADATA:
                # Give the store sync a head start so IGDB has fresh games to match
                time.sleep(120)
                _run_igdb_sync()
        except Exception as e:
            print(f"[scheduler] Error in scheduler loop: {e}")

        time.sleep(interval_seconds)


def start_scheduler():
    """Call once at app startup. No-op unless AUTO_SYNC_ENABLED=true."""
    if not AUTO_SYNC_ENABLED:
        print("[scheduler] Auto-sync disabled (set AUTO_SYNC_ENABLED=true to enable)")
        return

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()