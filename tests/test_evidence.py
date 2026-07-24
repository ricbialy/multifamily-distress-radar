import unittest

from distress_radar.domain.evidence import (
    EvidenceItem,
    FreshnessStatus,
    ValueType,
)


class EvidenceTests(unittest.TestCase):
    def test_evidence_carries_required_provenance(self) -> None:
        evidence = EvidenceItem(
            field="tax_status",
            value="unpaid",
            source="authorized_tax_csv",
            source_record_id="tax-2024-123",
            source_url="file://authorized/taxes.csv#row=2",
            fetched_at="2026-07-24T12:00:00+00:00",
            freshness_status=FreshnessStatus.FRESH,
            confidence=0.95,
            value_type=ValueType.REPORTED,
        )
        self.assertEqual(evidence.to_dict()["value_type"], "reported")
        self.assertEqual(evidence.to_dict()["freshness_status"], "fresh")

    def test_unknown_is_explicit_not_false_or_clean(self) -> None:
        evidence = EvidenceItem.unknown(
            field="open_liens",
            source="miami_dade_clerk",
            source_record_id="run-42",
            fetched_at="2026-07-24T12:00:00+00:00",
            reason="authentication_required",
        )
        self.assertIsNone(evidence.value)
        self.assertEqual(evidence.value_type, ValueType.UNKNOWN)
        self.assertEqual(evidence.freshness_status, FreshnessStatus.UNAVAILABLE)
        self.assertEqual(evidence.metadata["reason"], "authentication_required")

    def test_confidence_must_be_bounded(self) -> None:
        with self.assertRaises(ValueError):
            EvidenceItem(
                field="units",
                value=8,
                source="test",
                source_record_id="1",
                source_url=None,
                fetched_at="2026-07-24T12:00:00+00:00",
                freshness_status=FreshnessStatus.FRESH,
                confidence=1.1,
                value_type=ValueType.REPORTED,
            )


if __name__ == "__main__":
    unittest.main()
