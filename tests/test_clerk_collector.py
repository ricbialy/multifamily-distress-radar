import unittest
import json
import urllib.request
from dataclasses import replace
from unittest.mock import patch

from distress_radar.collectors.miami_dade_clerk import MiamiDadeClerkCollector, classify_document_type
from distress_radar.config import load_city_config


class ClerkCollectorTests(unittest.TestCase):
    def test_conservative_document_classification(self) -> None:
        self.assertEqual(classify_document_type("LIS PENDENS"), "lis_pendens")
        self.assertEqual(classify_document_type("CLAIM OF LIEN"), "lien")
        self.assertEqual(classify_document_type("SATISFACTION OF MORTGAGE"), "release")
        self.assertEqual(classify_document_type("MOR"), "mortgage")
        self.assertEqual(classify_document_type("DEED"), "other")

    def test_parses_document_without_exposing_key(self) -> None:
        config = load_city_config("hialeah_fl")
        config = replace(config, official_records_source=replace(config.official_records_source, enabled=True))
        with patch.dict("os.environ", {"MIAMI_DADE_CLERK_AUTH_KEY": "secret"}):
            collector = MiamiDadeClerkCollector(config)
        records = collector._parse("012345", {"OfficialRecordList": [{
            "CFN_MASTER_ID": "99", "DOC_TYPE": "LIS PENDENS", "REC_DATE": "20260102",
            "FIRST_PARTY": "BANK", "SECOND_PARTY": "OWNER", "CASE_NUM": "2026-1"
        }]}, "2026-01-02T00:00:00+00:00")
        self.assertEqual(records[0].signal_type, "lis_pendens")
        self.assertEqual(records[0].folio, "012345")
        self.assertNotIn("secret", records[0].source_url)

    def test_application_level_failure_is_not_silent(self) -> None:
        config = load_city_config("hialeah_fl")
        config = replace(config, official_records_source=replace(config.official_records_source, enabled=True))
        with patch.dict("os.environ", {"MIAMI_DADE_CLERK_AUTH_KEY": "secret"}):
            collector = MiamiDadeClerkCollector(config)
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = (
            b"<OfficialRecordsResponse><Status>Failed</Status>"
            b"<StatusDesc>IP Not Authorized</StatusDesc></OfficialRecordsResponse>"
        )
        with patch.object(urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "IP Not Authorized"):
                collector._request("012345")

    def test_xml_contains_official_record_list(self) -> None:
        payload = MiamiDadeClerkCollector._parse_xml(b"""
        <OfficialRecordsResponse><Status>Successful</Status><UnitsBalance>96</UnitsBalance>
        <OfficialRecordList><OfficialRecords><CFN_MASTER_ID>7</CFN_MASTER_ID>
        <DOC_TYPE>LIEN</DOC_TYPE><REC_BOOK>123</REC_BOOK></OfficialRecords></OfficialRecordList>
        </OfficialRecordsResponse>""")
        self.assertEqual(payload["Status"], "Successful")
        self.assertEqual(payload["OfficialRecordList"][0]["CFN_MASTER_ID"], "7")


if __name__ == "__main__":
    unittest.main()
