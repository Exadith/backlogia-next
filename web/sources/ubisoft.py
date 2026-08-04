import requests
import base64

UBI_LOGIN_URL = "https://public-ubiservices.ubi.com/v3/profiles/sessions"
UBI_ENTITLEMENTS_URL = "https://public-ubiservices.ubi.com/v1/profiles/{profileId}/entitlements"

APP_ID = "e3d5ea9e-50bd-43b7-88bf-39794f4e3d40"
# APP_ID = "3587dcaa-f77c0454f0b657-5d9037ffb4af"
CLIENT_SECRET = "da29d7c2-2028-4fe7-88bd-af20a8423da6"

basic_token = base64.b64encode(f"{APP_ID}:{CLIENT_SECRET}".encode()).decode()

BASE_HEADERS = {
    "Ubi-AppId": APP_ID,
    "Ubi-RequestedPlatformType": "uplay",
    "Content-Type": "application/json",
    "User-Agent": "UbiServices_SDK_2020.Release.58_PC64_ansi_static",
    "Authorization": f"Basic {basic_token}",
}


def ubisoft_login(email, password):
    payload = {
        "email": email,
        "password": password,
        "rememberMe": True
    }

    resp = requests.post(UBI_LOGIN_URL, headers=BASE_HEADERS, json=payload)

    if resp.status_code != 200:
        print("Login failed:", resp.text)
        return None

    data = resp.json()

    return {
        "ticket": data["ticket"],
        "sessionId": data["sessionId"],
        "profileId": data["profileId"]
    }


def get_owned_games(auth):
    headers = BASE_HEADERS.copy()
    headers["Authorization"] = f"Ubi_v1 t={auth['ticket']}"
    headers["Ubi-SessionId"] = auth["sessionId"]

    url = UBI_ENTITLEMENTS_URL.format(profileId=auth["profileId"])
    params = {
        "mediaType": "GAME",
        "platformType": "uplay"
    }

    resp = requests.get(url, headers=headers, params=params)

    if resp.status_code != 200:
        print("Failed to fetch entitlements:", resp.text)
        return []

    return resp.json().get("entitlements", [])


def main():
    email = input("Ubisoft Email: ")
    password = input("Ubisoft Password: ")

    print("Logging in...")
    auth = ubisoft_login(email, password)
    if not auth:
        return

    print("Fetching owned games...")
    entitlements = get_owned_games(auth)

    print("\n=== Owned Ubisoft Games ===")
    for ent in entitlements:
        name = ent.get("game", {}).get("name") or ent.get("productId")
        print(f"- {name}")

    print(f"\nTotal games: {len(entitlements)}")


if __name__ == "__main__":
    main()
