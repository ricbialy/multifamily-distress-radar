from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from email.message import EmailMessage
from io import StringIO
from pathlib import Path

from distress_radar.sources.mls.matrix_csv import (
    MatrixCsvImporter,
    MatrixSchemaChangedError,
    deduplicate_candidates,
    detect_listing_changes,
)
from distress_radar.sources.mls.matrix_email import LocalMatrixInbox

FIXTURE = Path(__file__).parent / "fixtures" / "matrix_20.csv"


class MatrixScoutTests(unittest.TestCase):
    def test_real_export_headers_map_and_bad_row_is_ledgered(self) -> None:
        payload = (
            ",MLS # Link,St,Area,Address,,Current Price,ZN,Year Built,"
            "Prop Type,Style of Property,Prop Type/Type of Building,"
            "Type of Property,Property SqFt,Waterfront Property (Y/N),"
            "Sale Price,#Bays\n"
            '1,A123,A,41,100 Test Ave,,"$2,500,000",3901,1970,COM/Sale,,'
            "Commercial/Residential Income,Income/MultiFamily,12000,,,,\n"
            '2,,A,41,200 Test Ave,,"$1,500,000",3901,1960,COM/Sale,,'
            "Commercial/Residential Income,Income/MultiFamily,9000,,,,\n"
        )
        importer = MatrixCsvImporter()
        batch = importer.import_reader(
            StringIO(payload),
            fetched_at="2026-07-24T12:00:00+00:00",
            source_url="manual-import://synthetic-header-test.csv",
        )

        self.assertEqual(batch.header_mapping["mls_number"], "MLS # Link")
        self.assertEqual(batch.header_mapping["state"], "St")
        self.assertEqual(batch.header_mapping["list_price"], "Current Price")
        self.assertEqual(batch.header_mapping["property_class"], "Type of Property")
        self.assertEqual(len(batch.accepted), 1)
        self.assertEqual(batch.accepted[0].source_record_id, "A123")
        self.assertEqual(batch.accepted[0].list_price, 2_500_000)
        self.assertEqual([row.status for row in batch.ledger], ["accepted", "rejected"])
        self.assertIn("MLS", batch.ledger[1].reason or "")

    def test_imports_existing_twenty_property_fixture(self) -> None:
        listings = MatrixCsvImporter().import_file(
            FIXTURE, fetched_at="2026-07-24T12:00:00+00:00"
        )
        self.assertEqual(len(listings), 20)
        self.assertEqual(listings[0].folio, "0431010010010")
        self.assertEqual(listings[0].units, 4)
        self.assertEqual(listings[0].source_record_id, "A1001")

    def test_schema_change_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.csv"
            path.write_text("Address,Price\n123 Main St,100\n", encoding="utf-8")
            with self.assertRaises(MatrixSchemaChangedError) as caught:
                MatrixCsvImporter().import_file(
                    path, fetched_at="2026-07-24T12:00:00+00:00"
                )
        self.assertIn("MLS number", str(caught.exception))

    def test_detects_material_listing_changes(self) -> None:
        previous = MatrixCsvImporter().import_file(
            FIXTURE, fetched_at="2026-07-23T12:00:00+00:00"
        )[2]
        current = replace(
            previous,
            status="Active",
            list_price=1_150_000,
            dom=35,
            cdom=40,
            noi=82_000,
            remarks="Seller motivated after buyer financing failed",
            fetched_at="2026-07-24T12:00:00+00:00",
        )
        changes = detect_listing_changes(previous, current)
        kinds = {change.change_type for change in changes}
        self.assertTrue(
            {
                "price_change",
                "status_change",
                "back_on_market",
                "contract_failure",
                "dom_increase",
                "cdom_increase",
                "noi_change",
                "motivated_seller_language",
            }.issubset(kinds)
        )

    def test_new_withdrawn_and_expired_events_are_explicit(self) -> None:
        listing = MatrixCsvImporter().import_file(
            FIXTURE, fetched_at="2026-07-24T12:00:00+00:00"
        )[0]
        self.assertEqual(detect_listing_changes(None, listing)[0].change_type, "new_listing")
        withdrawn = replace(listing, status="Withdrawn")
        expired = replace(listing, status="Expired")
        self.assertIn(
            "withdrawal",
            {change.change_type for change in detect_listing_changes(listing, withdrawn)},
        )
        self.assertIn(
            "expiration",
            {change.change_type for change in detect_listing_changes(listing, expired)},
        )

    def test_cross_class_candidates_group_by_folio_before_address(self) -> None:
        first = MatrixCsvImporter().import_file(
            FIXTURE, fetched_at="2026-07-24T12:00:00+00:00"
        )[0]
        duplicate = replace(
            first,
            source_record_id="C2001",
            property_class="Commercial",
            address="101 Palm Avenue",
        )
        groups = deduplicate_candidates((first, duplicate))
        self.assertEqual(len(groups), 1)
        self.assertEqual({item.source_record_id for item in groups[0]}, {"A1001", "C2001"})

    def test_local_inbox_reads_csv_and_eml_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            direct = inbox / "direct.csv"
            direct.write_bytes(FIXTURE.read_bytes())
            message = EmailMessage()
            message["Subject"] = "Matrix saved search"
            message.set_content("Attached export")
            message.add_attachment(
                FIXTURE.read_bytes(),
                maintype="text",
                subtype="csv",
                filename="matrix-email.csv",
            )
            (inbox / "saved-search.eml").write_bytes(message.as_bytes())
            attachments = LocalMatrixInbox(inbox).attachments()
        self.assertEqual(
            {attachment.filename for attachment in attachments},
            {"direct.csv", "matrix-email.csv"},
        )
        self.assertTrue(all(attachment.payload.startswith(b"MLS Number") for attachment in attachments))


if __name__ == "__main__":
    unittest.main()
