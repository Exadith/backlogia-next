from .telegram import send_message


def notify_new_games(store: str, games: list[str]) -> None:
    """
    Send Telegram notification about newly added games.
    """

    if not games:
        return

    games_list = "\n".join(f"🎮 {name}" for name in games)

    message = (
        f"🎮 <b>Backlogia</b>\n\n"
        f"🟢 <b>{store} Sync</b>\n\n"
        f"➕ Dodano <b>{len(games)}</b> nowych gier\n\n"
        f"{games_list}"
    )

    send_message(message)