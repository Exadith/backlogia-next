import html

from .telegram import send_message

# Telegram caps messages at 4096 characters. A big first sync (hundreds of
# games) would silently fail to send as a single message, so large lists
# are split into multiple messages instead.
MAX_GAMES_PER_MESSAGE = 50

def games_word(count: int) -> str:
    if count == 1:
        return "nową grę"

    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "nowe gry"

    return "nowych gier"

def notify_new_games(store: str, games: list[str]) -> None:
    """
    Send Telegram notification(s) about newly added games.
    """

    if not games:
        return

    # Escape game titles - unescaped "&", "<", ">" break Telegram's HTML
    # parser (parse_mode="HTML") and silently fail to send the whole message.
    escaped_store = html.escape(store)
    escaped_games = [html.escape(name) for name in games]

    chunks = [
        escaped_games[i:i + MAX_GAMES_PER_MESSAGE]
        for i in range(0, len(escaped_games), MAX_GAMES_PER_MESSAGE)
    ]
    total_chunks = len(chunks)

    count = len(games)
    games_text = games_word(count)   

    for chunk_index, chunk in enumerate(chunks, 1):
        games_list = "\n".join(f"🎮 {name}" for name in chunk)
        part_label = (
            f"\n📄 <i>Część {chunk_index}/{total_chunks}</i>"
            if total_chunks > 1 else ""
        )

        message = (
            f"🎮 <b>Backlogia</b>\n\n"
            f"🟢 <b>Synchronizacja {escaped_store} zakończona</b>"
            f"{part_label}\n\n"
            f"➕ Dodano <b>{count}</b> {games_text}\n\n"
            f"{games_list}"
        )

        send_message(message)
