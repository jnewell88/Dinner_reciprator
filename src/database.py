"""Single local SQLite database for the app: saved recipes and encrypted settings."""
import sqlite3
import threading
from pathlib import Path

DATA_DIRECTORY = Path(__file__).resolve().parent.parent / "data"
DATABASE_PATH = DATA_DIRECTORY / "app.sqlite3"
# Earlier versions used one file per feature; their rows are moved into DATABASE_PATH once.
LEGACY_DATABASES = {
    "saved_recipes": DATA_DIRECTORY / "recipes.sqlite3",
    "encrypted_settings": DATA_DIRECTORY / "kroger_credentials.sqlite3",
}

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS saved_recipes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        source_recipe_id TEXT NOT NULL,
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        ingredients_json TEXT NOT NULL,
        instructions_json TEXT NOT NULL,
        saved_at TEXT NOT NULL,
        servings TEXT NOT NULL DEFAULT '',
        UNIQUE(source, source_recipe_id)
    )""",
    "CREATE TABLE IF NOT EXISTS encrypted_settings (setting TEXT PRIMARY KEY, value BLOB NOT NULL)",
    # Non-secret app preferences (JSON values), e.g. the chosen store.
    "CREATE TABLE IF NOT EXISTS preferences (name TEXT PRIMARY KEY, value TEXT NOT NULL)",
)

_initialized_path: Path | None = None
_initialize_lock = threading.Lock()


def _restrict_permissions() -> None:
    # The file holds encrypted credentials, so keep it owner-only where supported.
    for path, mode in ((DATA_DIRECTORY, 0o700), (DATABASE_PATH, 0o600)):
        try:
            path.chmod(mode)
        except OSError:
            pass


def _create_schema(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA:
        connection.execute(statement)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(saved_recipes)")}
    if "servings" not in columns:
        connection.execute("ALTER TABLE saved_recipes ADD COLUMN servings TEXT NOT NULL DEFAULT ''")


def _migrate_legacy(connection: sqlite3.Connection) -> None:
    for table, legacy_path in LEGACY_DATABASES.items():
        if not legacy_path.exists():
            continue
        connection.execute("ATTACH DATABASE ? AS legacy", (str(legacy_path),))
        try:
            has_table = connection.execute(
                "SELECT 1 FROM legacy.sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
            if has_table:
                legacy_columns = [row[1] for row in connection.execute(f"PRAGMA legacy.table_info({table})")]
                column_list = ", ".join(legacy_columns)
                connection.execute(
                    f"INSERT OR IGNORE INTO main.{table} ({column_list}) SELECT {column_list} FROM legacy.{table}"
                )
                key = "id" if table == "saved_recipes" else "setting"
                missing = connection.execute(
                    f"SELECT COUNT(*) FROM legacy.{table} WHERE {key} NOT IN (SELECT {key} FROM main.{table})"
                ).fetchone()[0]
                if missing:
                    raise RuntimeError(f"Could not move {missing} rows from {legacy_path.name}; left it in place.")
            connection.commit()
        finally:
            connection.execute("DETACH DATABASE legacy")
        legacy_path.unlink()


def initialize() -> None:
    global _initialized_path
    if _initialized_path == DATABASE_PATH and DATABASE_PATH.exists():
        return
    with _initialize_lock:
        DATA_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = sqlite3.connect(DATABASE_PATH)
        try:
            _create_schema(connection)
            connection.commit()
            _migrate_legacy(connection)
        finally:
            connection.close()
        _restrict_permissions()
        _initialized_path = DATABASE_PATH


def connect() -> sqlite3.Connection:
    """Open the app database (creating and migrating it on first use). Use as a context manager."""
    initialize()
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection
