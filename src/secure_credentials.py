import json
import os
import sqlite3
from pathlib import Path

import keyring
from cryptography.fernet import Fernet, InvalidToken


KEYRING_SERVICE = "DinnerReciprator"
KEYRING_USERNAME = "kroger-credentials-encryption-key"
DATABASE_PATH = Path(__file__).resolve().parent.parent / "data" / "kroger_credentials.sqlite3"


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


def _ensure_database() -> None:
    DATABASE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        DATABASE_PATH.parent.chmod(0o700)
    except OSError:
        pass
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS encrypted_settings (setting TEXT PRIMARY KEY, value BLOB NOT NULL)"
        )
    try:
        DATABASE_PATH.chmod(0o600)
    except OSError:
        pass


def save_kroger_credentials(client_id: str, client_secret: str) -> None:
    fernet = _fernet(create=True)
    if fernet is None:
        raise SecureCredentialError("Could not initialize the credential encryption key.")
    encrypted_value = fernet.encrypt(
        json.dumps({"client_id": client_id, "client_secret": client_secret}).encode("utf-8")
    )
    _ensure_database()
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            "INSERT INTO encrypted_settings(setting, value) VALUES(?, ?) "
            "ON CONFLICT(setting) DO UPDATE SET value = excluded.value",
            ("kroger", encrypted_value),
        )
    try:
        DATABASE_PATH.chmod(0o600)
    except OSError:
        pass


def load_kroger_credentials() -> tuple[str, str] | None:
    if not DATABASE_PATH.exists():
        return None
    with sqlite3.connect(DATABASE_PATH) as connection:
        row = connection.execute(
            "SELECT value FROM encrypted_settings WHERE setting = ?", ("kroger",)
        ).fetchone()
    if row is None:
        return None

    fernet = _fernet(create=False)
    if fernet is None:
        raise SecureCredentialError(
            "Saved credentials exist, but their OS keyring key is unavailable; refusing to read unencrypted credentials."
        )
    try:
        credentials = json.loads(fernet.decrypt(row[0]).decode("utf-8"))
        return credentials["client_id"], credentials["client_secret"]
    except (InvalidToken, KeyError, TypeError, ValueError) as error:
        raise SecureCredentialError("Saved Kroger credentials could not be decrypted.") from error


def clear_kroger_credentials() -> None:
    if not DATABASE_PATH.exists():
        return
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute("DELETE FROM encrypted_settings WHERE setting = ?", ("kroger",))