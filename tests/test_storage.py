import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from storage import DEFAULT_FILTERS, Storage


class StorageMultiSourceTests(unittest.TestCase):
    def test_old_filters_are_merged_with_new_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.add_subscriber(42)
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "UPDATE subscribers SET filters = ? WHERE chat_id = ?",
                    (json.dumps({"cenado": "700"}), 42),
                )
                connection.commit()
            finally:
                connection.close()

            filters = storage.get_filters(42)
            self.assertEqual(filters["cenado"], "700")
            self.assertEqual(filters["sources"], "bazos,olx")
            self.assertEqual(filters["olx_location"], DEFAULT_FILTERS["olx_location"])

    def test_source_and_currency_are_saved_with_ad(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.add_subscriber(42)
            storage.mark_seen_with_meta(
                42,
                "olx:123",
                title="Квартира",
                price=20000,
                url="https://www.olx.ua/d/test.html",
                source="olx",
                currency="UAH",
            )

            ad = storage.get_ad(42, "olx:123")
            self.assertEqual(ad["source"], "olx")
            self.assertEqual(ad["currency"], "UAH")

    def test_primed_sources_are_tracked_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.add_subscriber(42)
            self.assertFalse(storage.is_source_primed(42, "olx"))
            storage.mark_source_primed(42, "olx")
            self.assertTrue(storage.is_source_primed(42, "olx"))
            self.assertFalse(storage.is_source_primed(42, "bazos"))

    def test_restart_recovers_prime_state_from_each_seen_source(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.add_subscriber(42)
            storage.prime_seen(42, ["olx:123"], source="olx")

            restarted = Storage(db_path)
            self.assertTrue(restarted.is_source_primed(42, "olx"))
            self.assertFalse(restarted.is_source_primed(42, "bazos"))


if __name__ == "__main__":
    unittest.main()
