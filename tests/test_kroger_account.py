import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from fastapi import HTTPException

import api
from src import database, secure_credentials
from src.kroger_client import KrogerAccountAuthorizationError


class KrogerAccountTests(unittest.TestCase):
    def setUp(self):
        fernet = Fernet(Fernet.generate_key())
        patcher = mock.patch.object(secure_credentials, "_fernet", return_value=fernet)
        patcher.start()
        self.addCleanup(patcher.stop)
        with database.connect() as connection:
            connection.execute("DELETE FROM encrypted_settings")
        secure_credentials.save_kroger_credentials("client-id", "client-secret")
        api._pending_account_states.clear()

    def tokens(self, access="access-1", refresh="refresh-1", expires_in=1800):
        return {"access_token": access, "refresh_token": refresh, "expires_at": time.time() + expires_in}

    def start_connect(self, return_to="http://127.0.0.1:5173/saved"):
        response = api.connect_kroger_account(api.KrogerAccountConnectRequest(return_to=return_to))
        query = parse_qs(urlparse(response["authorize_url"]).query)
        self.assertEqual(query["client_id"], ["client-id"])
        self.assertEqual(query["scope"], ["cart.basic:write"])
        self.assertEqual(query["redirect_uri"], [api.KROGER_REDIRECT_URI])
        return query["state"][0]

    def test_connect_flow_stores_tokens_without_exposing_them(self):
        state = self.start_connect()
        with mock.patch.object(api, "exchange_account_code", return_value=self.tokens()) as exchange:
            response = api.kroger_account_callback(state=state, code="auth-code")
        exchange.assert_called_once_with("auth-code")
        self.assertEqual(response.headers["location"], "http://127.0.0.1:5173/?kroger_account=connected")
        status = api.get_kroger_account()
        self.assertEqual(status, {"connected": True, "redirect_uri": api.KROGER_REDIRECT_URI})
        self.assertNotIn("access-1", repr(status))

    def test_state_is_single_use_and_required(self):
        state = self.start_connect()
        with mock.patch.object(api, "exchange_account_code", return_value=self.tokens()):
            api.kroger_account_callback(state=state, code="auth-code")
            with self.assertRaises(HTTPException):
                api.kroger_account_callback(state=state, code="auth-code")
            with self.assertRaises(HTTPException):
                api.kroger_account_callback(state="forged", code="auth-code")

    def test_denied_sign_in_returns_to_app(self):
        state = self.start_connect()
        response = api.kroger_account_callback(state=state, error="access_denied")
        self.assertTrue(response.headers["location"].endswith("?kroger_account=denied"))
        self.assertFalse(api.get_kroger_account()["connected"])

    def test_return_address_must_be_local(self):
        with self.assertRaises(HTTPException):
            api.connect_kroger_account(api.KrogerAccountConnectRequest(return_to="https://evil.example/"))

    def test_cart_add_uses_fresh_token(self):
        secure_credentials.save_kroger_account_tokens(self.tokens())
        with mock.patch.object(api.KrogerAPI, "add_items_to_cart") as add:
            result = api.add_to_cart(api.CartAddRequest(items=[
                {"upc": "0001111060933", "quantity": 2}, {"upc": "0001111041700"},
            ]))
        add.assert_called_once_with("access-1", [
            {"upc": "0001111060933", "quantity": 2, "modality": "PICKUP"},
            {"upc": "0001111041700", "quantity": 1, "modality": "PICKUP"},
        ])
        self.assertEqual(result, {"added": 2, "packages": 3})

    def test_expired_token_is_refreshed_and_saved(self):
        secure_credentials.save_kroger_account_tokens(self.tokens(expires_in=-5))
        with mock.patch.object(api, "refresh_account_token", return_value=self.tokens("access-2", "refresh-2")) as refresh, \
                mock.patch.object(api.KrogerAPI, "add_items_to_cart") as add:
            api.add_to_cart(api.CartAddRequest(items=[{"upc": "0001111060933"}]))
        refresh.assert_called_once_with("refresh-1")
        self.assertEqual(add.call_args.args[0], "access-2")
        self.assertEqual(secure_credentials.load_kroger_account_tokens()["refresh_token"], "refresh-2")

    def test_rejected_refresh_disconnects(self):
        secure_credentials.save_kroger_account_tokens(self.tokens(expires_in=-5))
        with mock.patch.object(api, "refresh_account_token", side_effect=KrogerAccountAuthorizationError("no")):
            with self.assertRaises(HTTPException) as raised:
                api.add_to_cart(api.CartAddRequest(items=[{"upc": "0001111060933"}]))
        self.assertEqual(raised.exception.status_code, 401)
        self.assertIsNone(secure_credentials.load_kroger_account_tokens())

    def test_revoked_token_retried_once_after_refresh(self):
        secure_credentials.save_kroger_account_tokens(self.tokens())
        with mock.patch.object(api, "refresh_account_token", return_value=self.tokens("access-2")), \
                mock.patch.object(api.KrogerAPI, "add_items_to_cart",
                                  side_effect=[KrogerAccountAuthorizationError("revoked"), None]) as add:
            api.add_to_cart(api.CartAddRequest(items=[{"upc": "0001111060933"}]))
        self.assertEqual([call.args[0] for call in add.call_args_list], ["access-1", "access-2"])

    def test_not_connected(self):
        with self.assertRaises(HTTPException) as raised:
            api.add_to_cart(api.CartAddRequest(items=[{"upc": "0001111060933"}]))
        self.assertEqual(raised.exception.status_code, 401)

    def test_changing_app_credentials_disconnects_account(self):
        secure_credentials.save_kroger_account_tokens(self.tokens())
        api.put_kroger_settings(api.KrogerCredentialsRequest(client_id="client-id", client_secret="new-secret"))
        self.assertTrue(api.get_kroger_account()["connected"])
        api.put_kroger_settings(api.KrogerCredentialsRequest(client_id="other-app", client_secret="x"))
        self.assertFalse(api.get_kroger_account()["connected"])

    def test_store_preference_round_trip(self):
        with database.connect() as connection:
            connection.execute("DELETE FROM preferences")
        self.assertEqual(api.get_store_preference(), {"store": None})
        api.put_store_preference(api.StorePreference(id="01400376", name="Dillons Marketplace"))
        self.assertEqual(api.get_store_preference(), {"store": {"id": "01400376", "name": "Dillons Marketplace"}})

    def test_purchase_quantity(self):
        self.assertEqual(api.purchase_quantity("3 packs (15 oz each)"), 3)
        self.assertEqual(api.purchase_quantity("1 pack (8 oz each)"), 1)
        self.assertEqual(api.purchase_quantity("1 lb by weight"), 1)
        self.assertEqual(api.purchase_quantity(None), 1)


if __name__ == "__main__":
    unittest.main()
