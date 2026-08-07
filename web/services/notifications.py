from .telegram import send_message


def notify_new_games(store: str, games: list[str]):
    """
    Send Telegram notification about newly added games.
    """

    if not games:
        return

    text = (
        "🎮 *Backlogia*\n\n"
        f"🟢 *Synchronizacja {store} zakończona*\n\n"
        f"➕ Dodano *{len(games)}* nowych gier\n\n"
    )

    for game in games:
        text += f"• {game}\n"

    send_message(text)