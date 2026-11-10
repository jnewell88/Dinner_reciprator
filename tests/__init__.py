"""Point every test at a throwaway data directory so the real data/ folder is never touched."""
import atexit
import shutil
import tempfile
from pathlib import Path

from src import database

_TEST_DATA = Path(tempfile.mkdtemp(prefix="dinner-tests-"))
database.DATA_DIRECTORY = _TEST_DATA
database.DATABASE_PATH = _TEST_DATA / "app.sqlite3"
database.LEGACY_DATABASES = {
    "saved_recipes": _TEST_DATA / "recipes.sqlite3",
    "encrypted_settings": _TEST_DATA / "kroger_credentials.sqlite3",
}
atexit.register(shutil.rmtree, _TEST_DATA, ignore_errors=True)
