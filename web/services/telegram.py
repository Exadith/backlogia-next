import requests

from .settings import get_telegram_settings


def send_message(text: str) -> bool:
    settings = get_telegram_settings()

    token = settings.get("bot_token")
    chat_id = settings.get("chat_id")

    if not token or not chat_id:
        print("[TELEGRAM] Missing configuration")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text,
        },
        timeout=15,
    )

    if response.ok:
        print("[TELEGRAM] Message sent")
    else:
        print("[TELEGRAM]", response.text)

    return response.ok