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
from .notifications import notify_new_games

# --- Configuration (all via environment variables, all optional) ----------

AUTO_SYNC_ENABLED = os.environ.get("AUTO_SYNC_ENABLED", "false").lower() == "true"
AUTO_SYNC_INTERVAL_HOURS = float(os.environ.get("AUTO_SYNC_INTERVAL_HOURS", "6"))
AUTO_SYNC_STORE = os.environ.get("AUTO_SYNC_STORE", "all")  # steam/epic/.../all

AUTO_SYNC_IGDB = os.environ.get("AUTO_SYNC_IGDB", "false").lower() == "true"
AUTO_SYNC_METACRITIC = os.environ.get("AUTO_SYNC_METACRITIC", "false").lower() == "true"
AUTO_SYNC_PROTONDB = os.environ.get("AUTO_SYNC_PROTONDB", "false").lower() == "true"
AUTO_SYNC_STEAMGRIDDB = os.environ.get("AUTO_SYNC_STEAMGRIDDB", "false").lower() == "true"

# Delay (seconds) before the app starts its very first auto-sync cycle,
# giving uvicorn/the DB time to finish initializing.
STARTUP_DELAY_SECONDS = 30

# Delay (seconds) between each metadata sync step, so each one has a
# reasonable chance to see the games the previous step just added/matched.
STEP_DELAY_SECONDS = 90

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


# --- Individual sync steps --------------------------------------------------

def _run_store_sync():
    if _has_active_job(JobType.STORE_SYNC.value):
        print("[scheduler] Skipping store sync - one is already running")
        return

    from .database_builder import create_database

    store_label = "all stores" if AUTO_SYNC_STORE == "all" else AUTO_SYNC_STORE
    job_id = create_job(JobType.STORE_SYNC, f"[Auto-sync] Starting {store_label} sync...")

    STORE_NAMES = {
        "steam": "Steam",
        "epic": "Epic Games",
        "gog": "GOG",
        "itch": "itch.io",
        "humble": "Humble Bundle",
        "amazon": "Amazon Games",
        "ea": "EA App",
        "battlenet": "Battle.net",
        "xbox": "Xbox",
        "local": "Local Games",
    }

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
                update_job_progress(
                    job_id,
                    i,
                    total,
                    f"Syncing {STORE_NAMES.get(name, name)}..."
                )
                try:
                    res = func(conn)
                    results[name] = res
                    
                    if isinstance(res, dict):
                        try:
                            notify_new_games(
                                STORE_NAMES.get(name, name),
                                res["added_games"],
                            )
                        except Exception as notify_err:
                            print(
                                f"[scheduler] Error sending Telegram notification for {name}: {notify_err}"
                            )

                except Exception as e:
                    results[name] = f"Error: {e}"

            # Keep GG.deals in step with the local library. The helper is a
            # no-op until the user configures a GGDEALS_TOKEN.
            from .ggdeals_sync import is_configured, sync_pending_games
            if is_configured():
                try:
                    results["ggdeals"] = sync_pending_games(
                        conn,
                        progress_callback=lambda current, total, message: update_job_progress(
                            job_id, current, total, message
                        ),
                    )
                except Exception as e:
                    print(f"[scheduler] GG.deals sync error: {e}")
                    results["ggdeals"] = {"error": str(e)}

            conn.close()

            # Podsumowanie liczby gier na potrzeby logowania i statusu zadania
            summary_results = {
                k: (v.get("count", 0) if isinstance(v, dict) else v)
                for k, v in results.items()
            }
            total_games = sum(v for v in summary_results.values() if isinstance(v, int))
            message = f"[Auto-sync] Synced {total_games} games: " + ", ".join(
                f"{s.capitalize()}: {c}" for s, c in summary_results.items()
            )
            complete_job(job_id, json.dumps(summary_results), message)
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
        except ValueError as e:
            fail_job(job_id, f"IGDB not configured: {e}")
        except Exception as e:
            fail_job(job_id, str(e))

    run_job_async(job_id, run)


def _run_metacritic_sync():
    if _has_active_job(JobType.METACRITIC_SYNC.value):
        print("[scheduler] Skipping Metacritic sync - one is already running")
        return

    from .metacritic_sync import (
        MetacriticClient, sync_games as metacritic_sync_games, add_metacritic_columns
    )

    job_id = create_job(JobType.METACRITIC_SYNC, "[Auto-sync] Starting Metacritic sync (missing)...")

    def run(job_id):
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row
            add_metacritic_columns(conn)
            client = MetacriticClient()
            matched, failed = metacritic_sync_games(
                conn, client, force=False,
                progress_callback=lambda c, t, m: update_job_progress(job_id, c, t, m)
            )
            conn.close()
            complete_job(
                job_id, json.dumps({"matched": matched, "failed": failed}),
                f"[Auto-sync] Metacritic sync complete: {matched} matched, {failed} failed"
            )
        except Exception as e:
            fail_job(job_id, str(e))

    run_job_async(job_id, run)


def _run_protondb_sync():
    if _has_active_job(JobType.PROTONDB_SYNC.value):
        print("[scheduler] Skipping ProtonDB sync - one is already running")
        return

    from .protondb_sync import (
        ProtonDBClient, sync_games as protondb_sync_games, add_protondb_columns
    )

    job_id = create_job(JobType.PROTONDB_SYNC, "[Auto-sync] Starting ProtonDB sync (missing)...")

    def run(job_id):
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row
            add_protondb_columns(conn)
            client = ProtonDBClient()
            matched, failed = protondb_sync_games(
                conn, client, force=False,
                progress_callback=lambda c, t, m: update_job_progress(job_id, c, t, m)
            )
            conn.close()
            complete_job(
                job_id, json.dumps({"matched": matched, "failed": failed}),
                f"[Auto-sync] ProtonDB sync complete: {matched} matched, {failed} failed"
            )
        except Exception as e:
            fail_job(job_id, str(e))

    run_job_async(job_id, run)


def _run_steamgriddb_sync():
    if _has_active_job(JobType.STEAMGRIDDB_SYNC.value):
        print("[scheduler] Skipping SteamGridDB sync - one is already running")
        return

    from .steamgriddb_sync import (
        SteamGridDBClient, sync_games as steamgriddb_sync_games, add_steamgriddb_columns
    )

    api_key = os.getenv("STEAMGRIDDB_API_KEY")
    if not api_key:
        print("[scheduler] Skipping SteamGridDB sync - STEAMGRIDDB_API_KEY not set")
        return

    job_id = create_job(JobType.STEAMGRIDDB_SYNC, "[Auto-sync] Starting SteamGridDB sync (missing)...")

    def run(job_id):
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row
            add_steamgriddb_columns(conn)
            client = SteamGridDBClient(api_key)
            matched, failed = steamgriddb_sync_games(
                conn, client, force=False,
                progress_callback=lambda c, t, m: update_job_progress(job_id, c, t, m)
            )
            conn.close()
            complete_job(
                job_id, json.dumps({"matched": matched, "failed": failed}),
                f"[Auto-sync] SteamGridDB sync complete: {matched} matched, {failed} failed"
            )
        except Exception as e:
            fail_job(job_id, str(e))

    run_job_async(job_id, run)


# --- Main loop ---------------------------------------------------------------

def _wait_for_job_type_to_finish(job_type_value, max_wait_seconds=1800):
    """Poll until no active job of the given type is running, or timeout."""
    waited = 0
    while _has_active_job(job_type_value) and waited < max_wait_seconds:
        time.sleep(10)
        waited += 10


def _loop():
    interval_seconds = AUTO_SYNC_INTERVAL_HOURS * 3600
    print(
        f"[scheduler] Auto-sync enabled: every {AUTO_SYNC_INTERVAL_HOURS}h | "
        f"store={AUTO_SYNC_STORE} | "
        f"igdb={AUTO_SYNC_IGDB} metacritic={AUTO_SYNC_METACRITIC} "
        f"protondb={AUTO_SYNC_PROTONDB} steamgriddb={AUTO_SYNC_STEAMGRIDDB}"
    )

    # Let the app finish starting up before the first run
    time.sleep(STARTUP_DELAY_SECONDS)

    while True:
        try:
            _run_store_sync()
            _wait_for_job_type_to_finish(JobType.STORE_SYNC.value)

            if AUTO_SYNC_IGDB:
                _run_igdb_sync()
                _wait_for_job_type_to_finish(JobType.IGDB_SYNC.value)
                time.sleep(STEP_DELAY_SECONDS)

            if AUTO_SYNC_METACRITIC:
                _run_metacritic_sync()
                _wait_for_job_type_to_finish(JobType.METACRITIC_SYNC.value)
                time.sleep(STEP_DELAY_SECONDS)

            if AUTO_SYNC_PROTONDB:
                _run_protondb_sync()
                _wait_for_job_type_to_finish(JobType.PROTONDB_SYNC.value)
                time.sleep(STEP_DELAY_SECONDS)

            if AUTO_SYNC_STEAMGRIDDB:
                _run_steamgriddb_sync()
                _wait_for_job_type_to_finish(JobType.STEAMGRIDDB_SYNC.value)

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
