import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import database


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="dinner-db-test-"))
        self.recipes = self.directory / "recipes.sqlite3"
        self.credentials = self.directory / "kroger_credentials.sqlite3"
        patches = {
            "DATA_DIRECTORY": self.directory,
            "DATABASE_PATH": self.directory / "app.sqlite3",
            "LEGACY_DATABASES": {"saved_recipes": self.recipes, "encrypted_settings": self.credentials},
            "_initialized_path": None,
        }
        for name, value in patches.items():
            patcher = mock.patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_legacy_files(self, with_servings=True):
        with sqlite3.connect(self.recipes) as connection:
            servings = ", servings TEXT NOT NULL DEFAULT ''" if with_servings else ""
            connection.execute(
                "CREATE TABLE saved_recipes (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, "
                "source_recipe_id TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL, "
                "ingredients_json TEXT NOT NULL, instructions_json TEXT NOT NULL, saved_at TEXT NOT NULL"
                f"{servings}, UNIQUE(source, source_recipe_id))"
            )
            connection.execute(
                "INSERT INTO saved_recipes (id, source, source_recipe_id, title, url, ingredients_json, "
                "instructions_json, saved_at) VALUES (4, 'budgetbytes', 'u', 'Enchiladas', 'u', '[]', '[]', 't')"
            )
        connection.close()
        with sqlite3.connect(self.credentials) as connection:
            connection.execute("CREATE TABLE encrypted_settings (setting TEXT PRIMARY KEY, value BLOB NOT NULL)")
            connection.execute("INSERT INTO encrypted_settings VALUES ('kroger', x'deadbeef')")
        connection.close()

    def test_rows_moved_ids_kept_and_old_files_removed(self):
        self.make_legacy_files()
        with database.connect() as connection:
            recipe = connection.execute("SELECT id, title, servings FROM saved_recipes").fetchone()
            setting = connection.execute("SELECT value FROM encrypted_settings").fetchone()
        self.assertEqual(tuple(recipe), (4, "Enchiladas", ""))
        self.assertEqual(bytes(setting[0]), bytes.fromhex("deadbeef"))
        self.assertFalse(self.recipes.exists())
        self.assertFalse(self.credentials.exists())
        self.assertEqual(oct(database.DATABASE_PATH.stat().st_mode & 0o777), "0o600")

    def test_migrates_recipes_saved_before_servings_column(self):
        self.make_legacy_files(with_servings=False)
        with database.connect() as connection:
            self.assertEqual(connection.execute("SELECT servings FROM saved_recipes").fetchone()[0], "")

    def test_fresh_install_creates_both_tables(self):
        with database.connect() as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"saved_recipes", "encrypted_settings"} <= tables)

    def test_failed_copy_keeps_old_file(self):
        self.make_legacy_files()
        with mock.patch.object(database, "_create_schema"), self.assertRaises(sqlite3.Error):
            database.initialize()
        self.assertTrue(self.recipes.exists())
        self.assertTrue(self.credentials.exists())


if __name__ == "__main__":
    unittest.main()
