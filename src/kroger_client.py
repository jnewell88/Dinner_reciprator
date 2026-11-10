import base64
import time
from urllib.parse import urlencode

import requests

from config import KROGER_ACCOUNT_SCOPE, KROGER_BASE_URL, KROGER_REDIRECT_URI
from src.secure_credentials import SecureCredentialError, load_kroger_credentials

class KrogerAuthenticationError(RuntimeError):
    pass


class KrogerAccountAuthorizationError(RuntimeError):
    """The linked shopper account's token was rejected; the user must reconnect."""


def _app_credentials() -> tuple[str, str]:
    credentials = load_kroger_credentials()
    if credentials is None:
        raise SecureCredentialError("Kroger credentials are not configured. Add them in Settings.")
    return credentials


def _basic_auth_header(client_id: str, client_secret: str) -> str:
    return "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


def account_authorization_url(state: str) -> str:
    client_id, _ = _app_credentials()
    query = urlencode({
        "scope": KROGER_ACCOUNT_SCOPE,
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": KROGER_REDIRECT_URI,
        "state": state,
    })
    return f"{KROGER_BASE_URL}/connect/oauth2/authorize?{query}"


def _account_token_request(data: dict) -> dict:
    client_id, client_secret = _app_credentials()
    response = requests.post(
        f"{KROGER_BASE_URL}/connect/oauth2/token",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": _basic_auth_header(client_id, client_secret),
        },
        data=data,
        timeout=20,
    )
    if response.status_code in (400, 401, 403):
        raise KrogerAccountAuthorizationError("Kroger rejected the account authorization. Reconnect your account.")
    response.raise_for_status()
    payload = response.json()
    if not payload.get("access_token"):
        raise KrogerAccountAuthorizationError("Kroger did not return an account access token.")
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token") or data.get("refresh_token"),
        "expires_at": time.time() + int(payload.get("expires_in") or 1800),
    }


def exchange_account_code(code: str) -> dict:
    return _account_token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": KROGER_REDIRECT_URI,
    })


def refresh_account_token(refresh_token: str) -> dict:
    return _account_token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})


class KrogerAPI:
    def __init__(self):
        self.access_token = None

    def authenticate_client(self, scope: str = "product.compact"):
        client_id, client_secret = _app_credentials()
        auth_url = f"{KROGER_BASE_URL}/connect/oauth2/token"

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": _basic_auth_header(client_id, client_secret),
        }
        data = {"grant_type": "client_credentials", "scope": scope}

        response = requests.post(auth_url, headers=headers, data=data)
        if response.status_code in (400, 401, 403):
            raise KrogerAuthenticationError(
                "Kroger rejected the saved client ID or secret. Check them in Settings."
            )
        response.raise_for_status()
        self.access_token = response.json().get("access_token")
        return self.access_token

    def search_product(
        self,
        term: str,
        location_id: str,
        limit: int = 5,
        brand: str | None = None,
    ):
        if not self.access_token:
            self.authenticate_client()

        url = f"{KROGER_BASE_URL}/products"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.access_token}"
        }
        params = {
            "filter.term": term,
            "filter.locationId": location_id,
            "filter.fulfillment": "curbside",
            "filter.limit": limit
        }
        if brand:
            params["filter.brand"] = brand

        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json().get("data", [])

    def search_locations(self, zip_code: str, limit: int = 10):
        if not self.access_token:
            self.authenticate_client(scope="product.compact")

        url = f"{KROGER_BASE_URL}/locations"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.access_token}"
        }
        params = {
            "filter.zipCode.near": zip_code,
            "filter.limit": limit
        }

        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json().get("data", [])

    def add_items_to_cart(self, user_bearer_token: str, items: list[dict]) -> None:
        """Add items ({upc, quantity, modality}) to the linked shopper's cart."""
        response = requests.put(
            f"{KROGER_BASE_URL}/cart/add",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {user_bearer_token}",
            },
            json={"items": items},
            timeout=20,
        )
        if response.status_code in (401, 403):
            raise KrogerAccountAuthorizationError("Kroger rejected the account token. Reconnect your account.")
        response.raise_for_status()
