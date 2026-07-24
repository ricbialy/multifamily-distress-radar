import unittest

from distress_radar.domain.owner import CanonicalOwner
from distress_radar.domain.property import CanonicalProperty
from distress_radar.identity.address_normalizer import normalize_address
from distress_radar.identity.folio_resolver import FolioResolver, normalize_folio
from distress_radar.identity.match_service import MatchService, MatchStatus
from distress_radar.identity.owner_resolver import OwnerResolver


class IdentityTests(unittest.TestCase):
    def test_normalizes_folio_and_address_without_dropping_unit(self) -> None:
        self.assertEqual(normalize_folio("01-2345-678-9010"), "0123456789010")
        self.assertEqual(
            normalize_address("123 North Main Street, Apt #2"),
            "123 n main st unit 2",
        )
        self.assertEqual(
            normalize_address("1440 SW 4th St"),
            normalize_address("1440 SW 4 ST"),
        )

    def test_folio_match_has_priority_over_address(self) -> None:
        existing = CanonicalProperty(
            property_id="property-1",
            folio="0123456789010",
            address="99 Other Street",
            municipality="Hialeah",
            jurisdiction="Miami-Dade",
        )
        result = MatchService().match(
            folio="01-2345-678-9010",
            address="123 Main Street",
            municipality="Hialeah",
            candidates=(existing,),
        )
        self.assertEqual(result.status, MatchStatus.CONFIRMED)
        self.assertEqual(result.method, "folio")
        self.assertEqual(result.property_id, "property-1")

    def test_folio_resolver_requires_one_exact_authoritative_match(self) -> None:
        resolver = FolioResolver()
        self.assertEqual(
            resolver.resolve("04-2025-001-0241", ("0420250010241",)).folio,
            "0420250010241",
        )
        self.assertIsNone(resolver.resolve(None, ()).folio)
        self.assertTrue(resolver.resolve(None, ("1", "2")).conflicting)
        self.assertTrue(
            resolver.resolve("0400000000001", ("0400000000002",)).conflicting
        )

    def test_exact_address_and_municipality_can_confirm_match(self) -> None:
        existing = CanonicalProperty(
            property_id="property-1",
            folio=None,
            address="123 N Main St",
            municipality="Hialeah",
            jurisdiction="Miami-Dade",
        )
        result = MatchService().match(
            folio=None,
            address="123 North Main Street",
            municipality="Hialeah",
            candidates=(existing,),
        )
        self.assertEqual(result.status, MatchStatus.CONFIRMED)
        self.assertEqual(result.method, "normalized_address")

    def test_fuzzy_address_is_review_only(self) -> None:
        existing = CanonicalProperty(
            property_id="property-1",
            folio=None,
            address="123 N Main Street",
            municipality="Hialeah",
            jurisdiction="Miami-Dade",
        )
        result = MatchService().match(
            folio=None,
            address="123 N Main Str",
            municipality="Hialeah",
            candidates=(existing,),
        )
        self.assertEqual(result.status, MatchStatus.REVIEW)
        self.assertEqual(result.method, "fuzzy_address")

    def test_owner_alias_resolution_does_not_claim_beneficial_ownership(self) -> None:
        owner = CanonicalOwner(
            owner_id="owner-1",
            display_name="Main Street Holdings LLC",
            entity_type="llc",
        )
        resolver = OwnerResolver((owner,))
        result = resolver.resolve("MAIN STREET HOLDINGS, L.L.C.")
        self.assertEqual(result.owner_id, "owner-1")
        self.assertTrue(result.alias_match)
        self.assertFalse(result.beneficial_ownership_confirmed)


if __name__ == "__main__":
    unittest.main()
