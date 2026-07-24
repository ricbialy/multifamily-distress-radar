import unittest

from distress_radar.identity.address_validation import (
    AddressCandidate,
    AddressValidationStatus,
    CountyAddressValidator,
)
from distress_radar.models import PropertyRecord


def _county_record(*, address: str = "101 PALM AVE") -> PropertyRecord:
    return PropertyRecord(
        city_slug="hialeah",
        source_name="miami_dade_property_point_view",
        folio="04-3101-001-0010",
        address=address,
        city="HIALEAH",
        zip_code="33010",
        owner_name=None,
        owner_name_2=None,
        mailing_address_1=None,
        mailing_address_2=None,
        mailing_city=None,
        mailing_state=None,
        mailing_zip=None,
        dor_code=None,
        dor_description=None,
        unit_count=None,
        year_built=None,
        floor_count=None,
        building_area=None,
        lot_size=None,
        assessed_value=None,
        last_sale_date=None,
        last_sale_price=None,
        latitude=25.836,
        longitude=-80.281,
        source_url="https://gis-mdc.opendata.arcgis.com/",
        fetched_at="2026-07-24T11:00:00+00:00",
    )


class CountyAddressValidatorTests(unittest.TestCase):
    def test_exact_folio_and_address_match_is_verified(self) -> None:
        result = CountyAddressValidator((_county_record(),)).validate(
            folio="0431010010010",
            candidates=(
                AddressCandidate(
                    source="matrix",
                    street="101 Palm Avenue",
                    municipality="Hialeah",
                    state="FL",
                    postal_code="33010",
                ),
            ),
            checked_at="2026-07-24T12:00:00+00:00",
        )

        self.assertEqual(result.status, AddressValidationStatus.VERIFIED)
        self.assertEqual(result.address, "101 PALM AVE, HIALEAH, FL 33010")
        self.assertEqual(result.source, "miami_dade_property_point_view")
        self.assertEqual(result.source_record_id, "0431010010010")
        self.assertEqual(result.latitude, 25.836)
        self.assertEqual(result.longitude, -80.281)

    def test_exact_folio_with_different_address_is_mismatch(self) -> None:
        result = CountyAddressValidator((_county_record(),)).validate(
            folio="04-3101-001-0010",
            candidates=(
                AddressCandidate(
                    source="matrix",
                    street="999 Wrong St",
                    municipality="Hialeah",
                    state="FL",
                    postal_code="33010",
                ),
            ),
            checked_at="2026-07-24T12:00:00+00:00",
        )

        self.assertEqual(result.status, AddressValidationStatus.MISMATCH)
        self.assertEqual(result.address, "101 PALM AVE, HIALEAH, FL 33010")
        self.assertIn("999 wrong st", result.details)

    def test_two_non_authoritative_sources_are_only_corroborated(self) -> None:
        result = CountyAddressValidator(()).validate(
            folio="0431010010010",
            candidates=(
                AddressCandidate(
                    source="matrix",
                    street="101 Palm Avenue",
                    municipality="Hialeah",
                    state="FL",
                    postal_code="33010",
                ),
                AddressCandidate(
                    source="off_market_csv",
                    street="101 Palm Ave",
                    municipality="Hialeah",
                    state="FL",
                    postal_code="33010",
                ),
            ),
            checked_at="2026-07-24T12:00:00+00:00",
        )

        self.assertEqual(result.status, AddressValidationStatus.CORROBORATED)
        self.assertEqual(result.source, "matrix + off_market_csv")
        self.assertFalse(result.is_authoritatively_verified)

    def test_single_source_without_county_record_is_unverified(self) -> None:
        result = CountyAddressValidator(()).validate(
            folio=None,
            candidates=(
                AddressCandidate(
                    source="matrix",
                    street="114 Palm Ave",
                    municipality="Hialeah",
                    state="FL",
                    postal_code="33010",
                ),
            ),
            checked_at="2026-07-24T12:00:00+00:00",
        )

        self.assertEqual(result.status, AddressValidationStatus.UNVERIFIED)
        self.assertIn("authoritative county record", result.details)


if __name__ == "__main__":
    unittest.main()
