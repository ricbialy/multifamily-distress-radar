import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from test_storage import sample_property

from distress_radar.recommendations.features import calculate_evidence_scores
from distress_radar.storage import RadarStore
from distress_radar.tax_import import import_tax_csv, is_unpaid_status


class TaxImportTests(unittest.TestCase):
    def test_negated_and_compound_cleared_statuses_are_not_unpaid(self) -> None:
        for status in (
            "not delinquent",
            "no outstanding balance",
            "paid - formerly delinquent",
            "released / outstanding record",
            "satisfied lien",
            "no unpaid balance",
            "not unpaid",
            "not currently unpaid",
        ):
            with self.subTest(status=status):
                self.assertFalse(is_unpaid_status(status))

        for status in ("unpaid", "delinquent", "past due", "not paid"):
            with self.subTest(status=status):
                self.assertTrue(is_unpaid_status(status))

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

    def test_unpaid_status_is_not_interpreted_as_paid(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "tax.csv"
            path.write_text(
                "folio,tax year,balance due,status\n"
                "14-2235-001-0020,2024,1200.00,Unpaid\n",
                encoding="utf-8",
            )
            records = import_tax_csv("surfside_fl", path)
            with RadarStore(Path(temporary) / "radar.sqlite3") as store:
                property_run = store.start_run("surfside_fl", "property", ())
                store.upsert_properties(property_run, (sample_property(),))
                run_id = store.start_run("surfside_fl", "tax_csv", ())
                store.upsert_tax_delinquencies(run_id, records)
                output = Path(temporary) / "opportunities.csv"
                store.export_opportunities_csv("surfside_fl", output, 1)
                with output.open(encoding="utf-8") as handle:
                    row = next(csv.DictReader(handle))
            self.assertEqual(row["financial_distress_score"], "35")

    def test_statusless_positive_balance_remains_actionable(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "tax.csv"
            path.write_text(
                "folio,tax year,balance due\n"
                "14-2235-001-0020,2024,1200.00\n",
                encoding="utf-8",
            )
            records = import_tax_csv("surfside_fl", path)
            self.assertIsNone(records[0].status)
            self.assertTrue(is_unpaid_status(records[0].status))

            with RadarStore(Path(temporary) / "radar.sqlite3") as store:
                property_run = store.start_run("surfside_fl", "property", ())
                store.upsert_properties(property_run, (sample_property(),))
                run_id = store.start_run("surfside_fl", "tax_csv", ())
                store.upsert_tax_delinquencies(run_id, records)
                output = Path(temporary) / "opportunities.csv"
                store.export_opportunities_csv("surfside_fl", output, 1)
                with output.open(encoding="utf-8") as handle:
                    row = next(csv.DictReader(handle))

            self.assertEqual(row["delinquent_tax_year_count"], "1")
            self.assertEqual(row["delinquent_tax_amount"], "1200.0")
            self.assertEqual(row["financial_distress_score"], "35")

    def test_statusless_tax_credit_does_not_create_motivation(self) -> None:
        shared = {
            "county": {},
            "listing": {},
            "municipal": (),
            "official_records": (),
            "missing_fields": (),
        }
        positive = calculate_evidence_scores(
            **shared,
            tax_records=({"amount_due": 125, "status": None},),
        )
        credit = calculate_evidence_scores(
            **shared,
            tax_records=({"amount_due": -125, "status": None},),
        )

        self.assertEqual(positive.dimensions.owner_motivation, 30)
        self.assertEqual(credit.dimensions.owner_motivation, 0)


if __name__ == "__main__":
    unittest.main()
