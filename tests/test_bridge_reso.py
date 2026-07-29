from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from contextlib import redirect_stdout
from pathlib import Path
from typing import Self
from unittest.mock import patch

from distress_radar.cli import build_parser, main
from distress_radar.intelligence_store import IntelligenceStore
from distress_radar.sources.base import (
    AuthorizationMode,
    CollectionErrorKind,
    SourceHealthState,
)
from distress_radar.sources.mls.bridge_reso import (
    BridgeApiError,
    BridgeCollectionResult,
    BridgeResoCollector,
    BridgeSchemaChangedError,
)


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def reso_listing(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "ListingKey": "bridge-key-1",
        "ListingId": "A123",
        "StandardStatus": "Active",
        "UnparsedAddress": "100 NW 1st St",
        "City": "Hialeah",
        "StateOrProvince": "FL",
        "PostalCode": "33010",
        "ParcelNumber": "04-3101-001-0010",
        "PropertyType": "Commercial Sale",
        "PropertySubType": "Multi Family",
        "ListPrice": 2_500_000,
        "DaysOnMarket": 12,
        "CumulativeDaysOnMarket": 19,
        "NumberOfUnitsTotal": 16,
        "NetOperatingIncome": 185_000,
        "GrossScheduledIncome": 300_000,
        "OperatingExpense": 115_000,
        "PublicRemarks": "Bridge generated test listing",
        "BridgeModificationTimestamp": "2026-07-29T12:00:00Z",
    }
    result.update(overrides)
    return result


class BridgeResoTests(unittest.TestCase):
    def test_source_contract_and_configuration_fail_closed(self) -> None:
        collector = BridgeResoCollector(
            dataset_id="test-dataset",
            token="server-token",
            opener=lambda request, timeout: FakeResponse({"value": []}),
        )

        self.assertEqual(
            collector.spec.authorization_mode, AuthorizationMode.AUTHORIZED_API
        )
        self.assertTrue(collector.spec.supports_pagination)
        self.assertTrue(collector.spec.preserves_raw_response)
        with self.assertRaisesRegex(ValueError, "BRIDGE_API_TOKEN"):
            BridgeResoCollector(dataset_id="test", token="")
        with self.assertRaisesRegex(ValueError, "dataset"):
            BridgeResoCollector(dataset_id="../other", token="server-token")
        secret = "do-not-leak-this-token"
        with self.assertRaises(ValueError) as caught:
            BridgeResoCollector(dataset_id="test", token=f"{secret}\n")
        self.assertNotIn(secret, str(caught.exception))

    def test_normalizes_reso_property_without_inventing_missing_values(self) -> None:
        collector = BridgeResoCollector(
            dataset_id="test",
            token="server-token",
            opener=lambda request, timeout: FakeResponse(
                {"value": [reso_listing(NetOperatingIncome=None)]}
            ),
        )

        result = collector.collect(
            top=20,
            max_pages=1,
            fetched_at="2026-07-29T13:00:00+00:00",
        )

        self.assertEqual(len(result.listings), 1)
        listing = result.listings[0]
        self.assertEqual(listing.source_name, "bridge_reso_test")
        self.assertEqual(listing.source_record_id, "bridge-key-1")
        self.assertTrue(listing.synthetic)
        self.assertTrue(result.synthetic)
        self.assertEqual(listing.address, "100 NW 1st St")
        self.assertEqual(listing.municipality, "Hialeah")
        self.assertEqual(listing.folio, "0431010010010")
        self.assertEqual(listing.property_class, "Commercial Sale / Multi Family")
        self.assertEqual(listing.list_price, 2_500_000)
        self.assertEqual(listing.units, 16)
        self.assertIsNone(listing.noi)
        self.assertEqual(listing.rents, 300_000)
        self.assertEqual(listing.expenses, 115_000)
        self.assertEqual(listing.raw_payload["ListingKey"], "bridge-key-1")
        self.assertNotIn("server-token", listing.source_url)
        self.assertEqual(len(result.raw_pages), 1)

    def test_paginates_with_stable_order_and_authorization_header(self) -> None:
        requests = []
        timeouts = []

        def opener(request: object, *, timeout: float) -> FakeResponse:
            requests.append(request)
            timeouts.append(timeout)
            query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(request.full_url).query
            )
            skip = int(query["$skip"][0])
            payload = (
                {
                    "value": [
                        reso_listing(ListingKey="key-1", ListingId="A1"),
                        reso_listing(ListingKey="key-2", ListingId="A2"),
                    ]
                }
                if skip == 0
                else {
                    "value": [
                        reso_listing(ListingKey="key-3", ListingId="A3")
                    ]
                }
            )
            return FakeResponse(payload)

        collector = BridgeResoCollector(
            dataset_id="test", token="server-token", opener=opener
        )
        result = collector.collect(
            top=2,
            max_pages=3,
            fetched_at="2026-07-29T13:00:00+00:00",
        )

        self.assertEqual(
            [listing.source_record_id for listing in result.listings],
            ["key-1", "key-2", "key-3"],
        )
        self.assertEqual(len(requests), 2)
        self.assertEqual(timeouts, [30, 30])
        for index, request in enumerate(requests):
            query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(request.full_url).query
            )
            self.assertEqual(int(query["$skip"][0]), index * 2)
            self.assertEqual(int(query["$top"][0]), 2)
            self.assertEqual(
                query["$orderby"][0],
                "BridgeModificationTimestamp asc,ListingKey asc",
            )
            self.assertEqual(
                request.get_header("Authorization"), "Bearer server-token"
            )
            self.assertNotIn("server-token", request.full_url)

    def test_listing_key_prevents_duplicate_listing_id_collisions(self) -> None:
        collector = BridgeResoCollector(
            dataset_id="test",
            token="server-token",
            opener=lambda request, timeout: FakeResponse(
                {
                    "value": [
                        reso_listing(ListingKey="key-one", ListingId="DUPLICATE"),
                        reso_listing(ListingKey="key-two", ListingId="DUPLICATE"),
                    ]
                }
            ),
        )

        result = collector.collect(top=20, max_pages=1)

        self.assertEqual(
            [listing.source_record_id for listing in result.listings],
            ["key-one", "key-two"],
        )
        self.assertEqual(
            [listing.raw_payload["ListingId"] for listing in result.listings],
            ["DUPLICATE", "DUPLICATE"],
        )

    def test_ordinary_pagination_cannot_cross_bridge_limit(self) -> None:
        collector = BridgeResoCollector(
            dataset_id="test",
            token="server-token",
            opener=lambda request, timeout: FakeResponse({"value": []}),
        )

        with self.assertRaisesRegex(ValueError, "10,000"):
            collector.collect(top=200, max_pages=51)

    def test_synthetic_records_cannot_enter_persistent_evidence(self) -> None:
        collector = BridgeResoCollector(
            dataset_id="test",
            token="server-token",
            opener=lambda request, timeout: FakeResponse(
                {"value": [reso_listing()]}
            ),
        )
        listing = collector.collect(top=20, max_pages=1).listings[0]

        self.assertTrue(listing.synthetic)
        with (
            tempfile.TemporaryDirectory() as temporary,
            IntelligenceStore(Path(temporary) / "radar.sqlite3") as store,
            self.assertRaisesRegex(ValueError, "Synthetic"),
        ):
            store.save_listing_snapshot(listing)

    def test_schema_and_nonfinite_values_fail_visibly(self) -> None:
        for payload in (
            {"value": [reso_listing(ListingKey=None)]},
            {"value": [reso_listing(UnparsedAddress=None)]},
            {"value": [reso_listing(ListPrice="NaN")]},
            {"records": []},
        ):
            with self.subTest(payload=payload):
                collector = BridgeResoCollector(
                    dataset_id="test",
                    token="server-token",
                    opener=lambda request, timeout, payload=payload: FakeResponse(
                        payload
                    ),
                )
                with self.assertRaises(BridgeSchemaChangedError):
                    collector.collect(top=20, max_pages=1)

    def test_http_failures_are_classified_without_exposing_token(self) -> None:
        for status, kind, health, retryable in (
            (
                401,
                CollectionErrorKind.AUTHENTICATION,
                SourceHealthState.AUTHENTICATION_REQUIRED,
                False,
            ),
            (
                429,
                CollectionErrorKind.RATE_LIMIT,
                SourceHealthState.DEGRADED,
                True,
            ),
            (
                400,
                CollectionErrorKind.VALIDATION,
                SourceHealthState.SCHEMA_CHANGED,
                False,
            ),
            (
                404,
                CollectionErrorKind.VALIDATION,
                SourceHealthState.SCHEMA_CHANGED,
                False,
            ),
        ):
            with self.subTest(status=status):

                def opener(
                    request: object,
                    timeout: float,
                    response_status: int = status,
                ) -> FakeResponse:
                    raise urllib.error.HTTPError(
                        request.full_url,
                        response_status,
                        "failure",
                        {},
                        io.BytesIO(b'{"error":{"message":"request rejected"}}'),
                    )

                collector = BridgeResoCollector(
                    dataset_id="test", token="server-token", opener=opener
                )
                with self.assertRaises(BridgeApiError) as caught:
                    collector.collect(top=20, max_pages=1)
                self.assertEqual(caught.exception.error.kind, kind)
                self.assertEqual(caught.exception.error.health_state, health)
                self.assertEqual(caught.exception.error.retryable, retryable)
                self.assertNotIn("server-token", str(caught.exception))

    def test_bridge_test_cli_exports_normalized_payload_without_token(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(
            [
                "bridge-test",
                "--dataset",
                "test",
                "--output",
                "bridge-test.json",
                "--top",
                "20",
                "--max-pages",
                "1",
            ]
        )
        self.assertEqual(parsed.command, "bridge-test")

        collector = BridgeResoCollector(
            dataset_id="test",
            token="server-token",
            opener=lambda request, timeout: FakeResponse(
                {"value": [reso_listing()]}
            ),
        )
        result = collector.collect(
            top=20,
            max_pages=1,
            fetched_at="2026-07-29T13:00:00+00:00",
        )
        with tempfile.TemporaryDirectory() as temporary:
            output_path = Path(temporary) / "bridge-test.json"
            stdout = io.StringIO()
            with (
                patch.dict(
                    "os.environ",
                    {
                        "BRIDGE_API_TOKEN": "server-token",
                        "BRIDGE_DATASET_ID": "test",
                    },
                    clear=True,
                ),
                patch(
                    "distress_radar.sources.mls.bridge_reso.BridgeResoCollector.collect",
                    return_value=BridgeCollectionResult(
                        listings=result.listings,
                        raw_pages=result.raw_pages,
                    ),
                ),
                redirect_stdout(stdout),
            ):
                main(
                    [
                        "bridge-test",
                        "--output",
                        str(output_path),
                        "--top",
                        "20",
                        "--max-pages",
                        "1",
                    ]
                )

            exported = output_path.read_text(encoding="utf-8")
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["listing_count"], 1)
            self.assertEqual(summary["page_count"], 1)
            self.assertEqual(summary["dataset_id"], "test")
            self.assertNotIn("server-token", exported)
            self.assertTrue(json.loads(exported)["synthetic"])
            self.assertTrue(json.loads(exported)["listings"][0]["synthetic"])
            self.assertEqual(
                json.loads(exported)["listings"][0]["source_record_id"],
                "bridge-key-1",
            )


if __name__ == "__main__":
    unittest.main()
