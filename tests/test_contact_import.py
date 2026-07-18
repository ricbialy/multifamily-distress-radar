import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from distress_radar.contact_import import import_contacts_csv
from distress_radar.storage import RadarStore


class ContactImportTests(unittest.TestCase):
    def test_imports_attributed_contact_and_deduplicates(self) -> None:
        with TemporaryDirectory() as temp:
            path = Path(temp) / "contacts.csv"
            path.write_text(
                "Folio,Contact Name,Role,Email,Phone,Verification Status,Confidence,Source URL\n"
                "14-2235-001-0020,Jane Manager,manager,JANE@EXAMPLE.COM,305-555-0100,verified,0.9,https://example.test/source\n",
                encoding="utf-8",
            )
            records = import_contacts_csv("surfside_fl", path)
            self.assertEqual(records[0].folio, "1422350010020")
            self.assertEqual(records[0].email, "jane@example.com")
            with RadarStore(Path(temp) / "radar.sqlite3") as store:
                first = store.upsert_property_contacts(records)
                second = store.upsert_property_contacts(records)
                contacts = store.property_contacts("surfside_fl", "1422350010020")
            self.assertEqual(first.new, 1)
            self.assertEqual(second.unchanged, 1)
            self.assertEqual(contacts[0]["verification_status"], "verified")


if __name__ == "__main__":
    unittest.main()
