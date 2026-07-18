from pathlib import Path
import unittest

from distress_radar.collectors.tyler_energov import TylerEnerGovCollector
from distress_radar.config import load_city_config


CONFIG_DIR = Path(__file__).resolve().parents[1] / "config" / "cities"


class FakeHttp:
    def request_json(self, method, path, payload=None):
        raise AssertionError(f"unexpected request: {method} {path}")


class CollectorParsingTests(unittest.TestCase):
    def test_status_catalog_trims_portal_whitespace(self) -> None:
        config = load_city_config("hialeah_fl", CONFIG_DIR)
        collector = TylerEnerGovCollector(config, http=FakeHttp())
        collector._prepared = True
        collector._tenant = {"TenantID": 1}
        collector._status_response = {
            "Result": [
                {"Name": "Warning Sent  ", "CodeCaseStatusId": "status-id"}
            ]
        }
        self.assertEqual(collector._status_map()["Warning Sent"], "status-id")

    def test_detail_fields_override_search_summary(self) -> None:
        config = load_city_config("surfside_fl", CONFIG_DIR)
        collector = TylerEnerGovCollector(config, http=FakeHttp())
        record = collector._parse_case(
            {
                "CaseId": "abc",
                "CaseNumber": "CC-1",
                "CaseType": "Search type",
                "CaseStatus": "In Progress",
                "ApplyDate": "2025-01-01T00:00:00",
                "AddressDisplay": "1 MAIN ST",
                "MainParcel": "14-22-35-001-0020",
            },
            fetched_at="2026-01-01T00:00:00+00:00",
            detail={
                "CodeCaseType": "Property Maintenance",
                "OpenedDate": "2025-01-02T00:00:00Z",
                "MainAddress": "1 MAIN ST, SURFSIDE FL",
                "Description": "Unsafe railing",
                "AssignedTo": "Inspector One",
            },
            violations=[{"ViolationNumber": "V-1"}],
        )
        self.assertEqual(record.case_type, "Property Maintenance")
        self.assertEqual(record.description, "Unsafe railing")
        self.assertEqual(record.parcel_number, "1422350010020")
        self.assertEqual(record.violation_count, 1)


if __name__ == "__main__":
    unittest.main()
