from pathlib import Path
from urllib.parse import urlparse

import requests
import shutil

DATA_DIR = Path("/data")
COVERS_DIR = DATA_DIR / "covers"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
BACKGROUNDS_DIR = DATA_DIR / "backgrounds"
IMAGE_HEADERS = {
    "User-Agent": "Backlogia/1.0",
}

def download_image(url: str, output_path: Path) -> bool:
    """
    Pobiera obraz z URL i zapisuje go pod output_path.

    Zwraca:
        True  - jeśli plik został zapisany
        False - jeśli pobieranie się nie udało
    """
    if not url:
      return False


    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        response = requests.get(
            url,
            headers=IMAGE_HEADERS,
            timeout=(10, 60),
        )
        response.raise_for_status()

        with open(output_path, "wb") as f:
            f.write(response.content)

        return True

    except Exception as e:
        print(f"Failed to download {url}: {e}")
        return False

def download_cover(
    url: str,
    game_id: int,
    background_url: str | None = None,
) -> dict | None:

    output = COVERS_DIR / f"{game_id}.jpg"

    cover = None
    background = None

    if output.exists() or download_image(url, output):
        cover = f"/covers/{output.name}"

    if background_url:
        background = download_background(background_url, game_id)

    if not cover:
        return None

    return {
        "cover": cover,
        "background": background,
    }

def download_background(url: str, game_id: int) -> str | None:
    if not url:
        return None

    ext = ".jpg"

    parsed = urlparse(url).path.lower()

    if parsed.endswith(".png"):
        ext = ".png"
    elif parsed.endswith(".webp"):
        ext = ".webp"

    output = BACKGROUNDS_DIR / f"{game_id}{ext}"

    if output.exists():
        return f"/backgrounds/{output.name}"

    if download_image(url, output):
        return f"/backgrounds/{output.name}"

    return None

def download_screenshots(urls: list[str], game_id: int) -> list[str]:
    result = []

    for index, url in enumerate(urls, start=1):

        output = SCREENSHOTS_DIR / str(game_id) / f"{index}.jpg"

        if output.exists() or download_image(url, output):
          result.append(f"/screenshots/{game_id}/{output.name}")

    return result

def get_cover_path(game_id: int) -> str:
    return f"/covers/{game_id}.jpg"


def get_screenshot_path(game_id: int, index: int) -> str:
    return f"/screenshots/{game_id}/{index}.jpg"

def clear_game_assets(game_id: int) -> None:
  """
  Usuwa lokalne assety gry:
    - okładkę
    - background
    - screenshoty
  """

  # Cover
  for ext in (".jpg", ".png", ".webp"):
      cover = COVERS_DIR / f"{game_id}{ext}"
      if cover.exists():
          cover.unlink()

  # Background
  for ext in (".jpg", ".png", ".webp"):
      background = BACKGROUNDS_DIR / f"{game_id}{ext}"
      if background.exists():
          background.unlink()

  # Screenshots
  screenshots = SCREENSHOTS_DIR / str(game_id)
  if screenshots.exists():
      shutil.rmtree(screenshots)