import json

import keyring
from cryptography.fernet import Fernet, InvalidToken

from src import database


KEYRING_SERVICE = "DinnerReciprator"
KEYRING_USERNAME = "kroger-credentials-encryption-key"


class SecureCredentialError(RuntimeError):
    pass


def _fernet(create: bool) -> Fernet | None:
    try:
        encoded_key = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        if encoded_key is None and create:
            encoded_key = Fernet.generate_key().decode("ascii")
            keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, encoded_key)
        if encoded_key is None:
            return None
        return Fernet(encoded_key.encode("ascii"))
    except Exception as error:
        raise SecureCredentialError(
            "The operating-system credential vault is unavailable. Unlock or configure it before saving Kroger credentials."
        ) from error


KROGER_APP_SETTING = "kroger"
KROGER_ACCOUNT_SETTING = "kroger_account"


def _save_encrypted(setting: str, value: dict) -> None:
    fernet = _fernet(create=True)
    if fernet is None:
        raise SecureCredentialError("Could not initialize the credential encryption key.")
    encrypted_value = fernet.encrypt(json.dumps(value).encode("utf-8"))
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO encrypted_settings(setting, value) VALUES(?, ?) "
            "ON CONFLICT(setting) DO UPDATE SET value = excluded.value",
            (setting, encrypted_value),
        )


def _load_encrypted(setting: str) -> dict | None:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value FROM encrypted_settings WHERE setting = ?", (setting,)
        ).fetchone()
    if row is None:
        return None

    fernet = _fernet(create=False)
    if fernet is None:
        raise SecureCredentialError(
            "Saved credentials exist, but their OS keyring key is unavailable; refusing to read unencrypted credentials."
        )
    try:
        value = json.loads(fernet.decrypt(row[0]).decode("utf-8"))
    except (InvalidToken, TypeError, ValueError) as error:
        raise SecureCredentialError("Saved Kroger credentials could not be decrypted.") from error
    if not isinstance(value, dict):
        raise SecureCredentialError("Saved Kroger credentials could not be decrypted.")
    return value


def _clear(setting: str) -> None:
    with database.connect() as connection:
        connection.execute("DELETE FROM encrypted_settings WHERE setting = ?", (setting,))


def save_kroger_credentials(client_id: str, client_secret: str) -> None:
    _save_encrypted(KROGER_APP_SETTING, {"client_id": client_id, "client_secret": client_secret})


def load_kroger_credentials() -> tuple[str, str] | None:
    credentials = _load_encrypted(KROGER_APP_SETTING)
    if credentials is None:
        return None
    try:
        return credentials["client_id"], credentials["client_secret"]
    except KeyError as error:
        raise SecureCredentialError("Saved Kroger credentials could not be decrypted.") from error


def clear_kroger_credentials() -> None:
    _clear(KROGER_APP_SETTING)


# Linked Dillons/Kroger shopper account (OAuth tokens). Never returned to the browser.
def save_kroger_account_tokens(tokens: dict) -> None:
    _save_encrypted(KROGER_ACCOUNT_SETTING, tokens)


def load_kroger_account_tokens() -> dict | None:
    return _load_encrypted(KROGER_ACCOUNT_SETTING)


def clear_kroger_account_tokens() -> None:
    _clear(KROGER_ACCOUNT_SETTING)
