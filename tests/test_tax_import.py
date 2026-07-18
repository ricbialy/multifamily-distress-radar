import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from distress_radar.storage import RadarStore
from distress_radar.tax_import import import_tax_csv
from test_storage import sample_property


class TaxImportTests(unittest.TestCase):
    def test_aliases_normalization_and_financial_score(self) -> None:
        with TemporaryDirectory() as temp:
            path = Path(temp) / "tax.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["Folio Number", "Tax Year", "Balance Due", "Payment Status"])
                writer.writeheader()
                writer.writerow({"Folio Number": "14-2235-001-0020", "Tax Year": "2025", "Balance Due": "$12,345.67", "Payment Status": "Delinquent"})
            records = import_tax_csv("surfside_fl", path)
            self.assertEqual(records[0].folio, "1422350010020")
            self.assertEqual(records[0].amount_due, 12345.67)
            db = Path(temp) / "radar.sqlite3"
            out = Path(temp) / "ranking.csv"
            with RadarStore(db) as store:
                prop_run = store.start_run("surfside_fl", "property", ())
                store.upsert_properties(prop_run, (sample_property(),))
                tax_run = store.start_run("surfside_fl", "tax", ())
                store.upsert_tax_delinquencies(tax_run, records)
                store.export_opportunities_csv("surfside_fl", out, 1)
            with out.open(encoding="utf-8") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["delinquent_tax_year_count"], "1")
            self.assertEqual(row["delinquent_tax_amount"], "12345.67")
            self.assertEqual(row["financial_distress_score"], "35")


if __name__ == "__main__":
    unittest.main()
