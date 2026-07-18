from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import csv
import unittest

from distress_radar.models import CodeCase, OfficialRecord, PropertyRecord
from distress_radar.storage import RadarStore


def sample_case(status: str = "In Progress") -> CodeCase:
    return CodeCase(
        city_slug="surfside_fl",
        source_name="test_source",
        source_record_id="abc",
        case_number="CC-1",
        case_type="Property Maintenance",
        status=status,
        opened_date="2025-01-01",
        closed_date=None,
        address="1 MAIN ST",
        parcel_number="1422350010020",
        description="Unsafe railing",
        project_name=None,
        assigned_to=None,
        violation_count=0,
        violations=(),
        source_url="https://example.test/case/abc",
        fetched_at="2026-01-01T00:00:00+00:00",
    )


def sample_property() -> PropertyRecord:
    return PropertyRecord(
        city_slug="surfside_fl",
        source_name="property_source",
        folio="1422350010020",
        address="1 MAIN ST",
        city="Surfside",
        zip_code="33154",
        owner_name="OWNER LLC",
        owner_name_2=None,
        mailing_address_1="PO BOX 1",
        mailing_address_2=None,
        mailing_city="MIAMI",
        mailing_state="FL",
        mailing_zip="33101",
        dor_code="0303",
        dor_description="MULTIFAMILY 10 UNITS PLUS",
        unit_count=24,
        year_built=1970,
        floor_count=3,
        building_area=20000,
        lot_size=30000,
        assessed_value=4000000,
        last_sale_date="20200101",
        last_sale_price=5000000,
        latitude=25.9,
        longitude=-80.1,
        source_url="https://example.test/properties",
        fetched_at="2026-01-01T00:00:00+00:00",
    )


class StorageTests(unittest.TestCase):
    def test_owner_key_normalizes_legal_suffix_and_punctuation(self) -> None:
        self.assertEqual(RadarStore.owner_key("Hudson Park Partners, L.L.C."), "hudson park partners")
        self.assertEqual(RadarStore.owner_key("OWNER LLC"), RadarStore.owner_key("Owner, Inc."))

    def test_property_lead_workflow_and_export(self) -> None:
        with TemporaryDirectory() as temp:
            database = Path(temp) / "radar.sqlite3"
            output = Path(temp) / "leads.csv"
            with RadarStore(database) as store:
                run = store.start_run("surfside_fl", "property_source", ())
                store.upsert_properties(run, (sample_property(),))
                lead = store.set_property_lead(
                    "surfside_fl", "1422350010020", stage="qualified",
                    assignee="ricardo", next_follow_up_date="2026-08-01",
                    notes="Confirm ownership",
                )
                self.assertEqual(lead["stage"], "qualified")
                self.assertEqual(store.export_leads_csv("surfside_fl", output), 1)
            with output.open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["assignee"], "ricardo")
            self.assertEqual(row["next_follow_up_date"], "2026-08-01")

    def test_property_lead_rejects_unknown_stage(self) -> None:
        with TemporaryDirectory() as temp, RadarStore(Path(temp) / "radar.sqlite3") as store:
            run = store.start_run("surfside_fl", "property_source", ())
            store.upsert_properties(run, (sample_property(),))
            with self.assertRaises(ValueError):
                store.set_property_lead("surfside_fl", "1422350010020", stage="maybe")

    def test_deduplicates_and_records_changes(self) -> None:
        with TemporaryDirectory() as temp:
            database = Path(temp) / "radar.sqlite3"
            output = Path(temp) / "cases.csv"
            with RadarStore(database) as store:
                run1 = store.start_run("surfside_fl", "test_source", ("In Progress",))
                first = store.upsert_cases(run1, (sample_case(),))
                store.finish_run(run1, "completed", 1)
                self.assertEqual((first.new, first.changed, first.unchanged), (1, 0, 0))

                run2 = store.start_run("surfside_fl", "test_source", ("In Progress",))
                second = store.upsert_cases(run2, (sample_case(),))
                self.assertEqual((second.new, second.changed, second.unchanged), (0, 0, 1))

                changed_case = replace(
                    sample_case(), status="Code Lien", fetched_at="2026-01-02T00:00:00+00:00"
                )
                run3 = store.start_run("surfside_fl", "test_source", ("Code Lien",))
                third = store.upsert_cases(run3, (changed_case,))
                self.assertEqual((third.new, third.changed, third.unchanged), (0, 1, 0))
                count = store.export_csv("surfside_fl", output)

            self.assertEqual(count, 1)
            with output.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["status"], "Code Lien")

    def test_properties_join_to_cases_and_rank(self) -> None:
        with TemporaryDirectory() as temp:
            database = Path(temp) / "radar.sqlite3"
            output = Path(temp) / "opportunities.csv"
            with RadarStore(database) as store:
                property_run = store.start_run(
                    "surfside_fl", "property_source", ("units:10-80",)
                )
                stats = store.upsert_properties(property_run, (sample_property(),))
                self.assertEqual(stats.new, 1)
                case_run = store.start_run("surfside_fl", "test_source", ("Lien",))
                store.upsert_cases(case_run, (sample_case("Lien"),))
                self.assertEqual(
                    store.top_property_folios("surfside_fl", 25), ["1422350010020"]
                )
                linked = store.cases_for_folios("surfside_fl", ["1422350010020"])
                self.assertEqual(linked[0].case_number, "CC-1")
                count = store.export_opportunities_csv("surfside_fl", output, 25)

            self.assertEqual(count, 1)
            with output.open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["folio"], "1422350010020")
            self.assertEqual(row["open_case_count"], "1")
            self.assertEqual(row["distress_score"], "30")
            self.assertEqual(row["opportunity_score"], "20")
            self.assertEqual(row["priority_score"], "50")

    def test_unmatched_lis_pendens_adds_financial_score(self) -> None:
        with TemporaryDirectory() as temp:
            database = Path(temp) / "radar.sqlite3"
            output = Path(temp) / "opportunities.csv"
            record = OfficialRecord(
                "surfside_fl", "clerk", "master-1", "2026-100", None, None,
                "1422350010020", "LP", "lis_pendens", "2026-01-01", None,
                "BANK", "OWNER LLC", "CASE-1", None, None, None, None,
                "https://example.test/docs", "2026-01-01T00:00:00+00:00",
            )
            with RadarStore(database) as store:
                prop_run = store.start_run("surfside_fl", "property_source", ())
                store.upsert_properties(prop_run, (sample_property(),))
                clerk_run = store.start_run("surfside_fl", "clerk", ())
                store.upsert_official_records(clerk_run, (record,))
                store.export_opportunities_csv("surfside_fl", output, 25)
                alerts = store.pending_alerts("surfside_fl")
                self.assertEqual(len(alerts), 1)
                self.assertEqual(alerts[0]["alert_type"], "lis_pendens")
                store.mark_alerts_delivered([alerts[0]["id"]])
                self.assertEqual(store.pending_alerts("surfside_fl"), [])
            with output.open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["financial_distress_score"], "40")
            self.assertEqual(row["active_lis_pendens_count"], "1")
            self.assertEqual(row["priority_score"], "60")

    def test_official_record_events_deduplicate_and_resolve(self) -> None:
        with TemporaryDirectory() as temp:
            database = Path(temp) / "radar.sqlite3"
            common = (
                "surfside_fl", "clerk", "1422350010020", "LP",
                "lis_pendens", "2026-01-01", None,
            )
            filing = OfficialRecord(
                common[0], common[1], "party-1", "2026-100", None, None,
                common[2], common[3], common[4], common[5], common[6],
                "BANK", "OWNER LLC", "CASE-1", None, None, None, None,
                "https://example.test/docs", "2026-01-02T00:00:00+00:00",
            )
            release = OfficialRecord(
                "surfside_fl", "clerk", "release-1", "2026-200", "2026-100", None,
                "1422350010020", "REL", "release", "2026-02-01", None,
                "BANK", "OWNER LLC", None, None, None, None, None,
                "https://example.test/docs", "2026-02-02T00:00:00+00:00",
            )
            with RadarStore(database) as store:
                run = store.start_run("surfside_fl", "clerk", ())
                store.upsert_official_records(run, (filing, release))
                events = store.official_record_events("surfside_fl", "1422350010020")
            filing_event = next(event for event in events if event["instrument_id"] == "2026-100")
            self.assertEqual(filing_event["lifecycle_status"], "resolved")
            self.assertEqual(filing_event["resolution_instrument_id"], "2026-200")
            self.assertEqual(filing_event["parties"], ["BANK", "OWNER LLC"])


if __name__ == "__main__":
    unittest.main()
