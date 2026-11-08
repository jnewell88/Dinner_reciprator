import requests
import base64
from config import KROGER_BASE_URL
from src.secure_credentials import SecureCredentialError, load_kroger_credentials

class KrogerAuthenticationError(RuntimeError):
    pass


class KrogerAPI:
    def __init__(self):
        self.access_token = None

    def authenticate_client(self, scope: str = "product.compact"):
        credentials = load_kroger_credentials()
        if credentials is None:
            raise SecureCredentialError("Kroger credentials are not configured. Add them in Settings.")
        client_id, client_secret = credentials
        auth_url = f"{KROGER_BASE_URL}/connect/oauth2/token"
        encoded_credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {encoded_credentials}"
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

    def add_items_to_cart(self, user_bearer_token: str, items: list):
        url = f"{KROGER_BASE_URL}/cart"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {user_bearer_token}"
        }
        payload = {"items": items}
        response = requests.put(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.status_code == 204
