import unittest

from distress_radar.domain.evidence import EvidenceItem, FreshnessStatus, ValueType
from distress_radar.recommendations.features import (
    RecommendationFeatures,
    ScoreDimensions,
)
from distress_radar.recommendations.rule_engine import (
    SUPPORTED_ACTIONS,
    recommend,
)


def evidence() -> EvidenceItem:
    return EvidenceItem(
        field="tax_status",
        value="unpaid",
        source="authorized_tax_csv",
        source_record_id="tax-1",
        source_url="manual-import://taxes.csv",
        fetched_at="2026-07-24T12:00:00+00:00",
        freshness_status=FreshnessStatus.FRESH,
        confidence=0.95,
        value_type=ValueType.REPORTED,
    )


class RecommendationTests(unittest.TestCase):
    def test_supported_actions_match_product_contract(self) -> None:
        self.assertEqual(
            SUPPORTED_ACTIONS,
            {
                "contact_owner",
                "contact_broker",
                "excluded",
                "verify_identity",
                "request_documents",
                "human_violation_review",
                "order_municipal_search",
                "watch",
                "reject",
                "reject_high_risk",
                "insufficient_data",
            },
        )

    def test_dimensions_remain_separate_and_explained(self) -> None:
        features = RecommendationFeatures(
            property_id="property-1",
            discovery_channels=("off_market",),
            scores=ScoreDimensions(
                owner_motivation=82,
                economics=78,
                market_pressure=0,
                property_risk=25,
                data_confidence=90,
                data_completeness=85,
                data_freshness=95,
            ),
            has_underwriting=True,
            critical_documents_missing=False,
            violation_review_required=False,
            municipal_search_required=False,
            why_now=("Unpaid 2024 taxes",),
            key_risks=("Insurance quote not bound",),
            missing_data=("beneficial ownership",),
            evidence=(evidence(),),
        )
        result = recommend(features)
        self.assertEqual(result.action, "contact_owner")
        self.assertEqual(result.scores.owner_motivation, 82)
        self.assertEqual(result.scores.economics, 78)
        self.assertIn("Why this property surfaced", result.explanation)
        self.assertIn("What could destroy the deal", result.explanation)
        self.assertEqual(result.evidence[0].source_record_id, "tax-1")

    def test_listed_opportunity_routes_to_broker(self) -> None:
        features = RecommendationFeatures.actionable(
            property_id="property-1", discovery_channels=("mls",)
        )
        self.assertEqual(recommend(features).action, "contact_broker")

    def test_unknown_coverage_routes_to_insufficient_data_not_clean(self) -> None:
        features = RecommendationFeatures.actionable(property_id="property-1")
        features = features.with_scores(data_completeness=30)
        self.assertEqual(recommend(features).action, "insufficient_data")

    def test_high_property_risk_is_not_hidden_by_motivation(self) -> None:
        features = RecommendationFeatures.actionable(property_id="property-1")
        features = features.with_scores(owner_motivation=100, property_risk=90)
        self.assertEqual(recommend(features).action, "reject_high_risk")

    def test_workflow_gates_choose_specific_human_action(self) -> None:
        base = RecommendationFeatures.actionable(property_id="property-1")
        self.assertEqual(
            recommend(base.with_flags(has_underwriting=False)).action,
            "insufficient_data",
        )
        self.assertEqual(
            recommend(base.with_flags(critical_documents_missing=True)).action,
            "request_documents",
        )
        self.assertEqual(
            recommend(base.with_flags(violation_review_required=True)).action,
            "human_violation_review",
        )
        self.assertEqual(
            recommend(base.with_flags(municipal_search_required=True)).action,
            "order_municipal_search",
        )

    def test_hard_gates_precede_scores_and_underwriting_status(self) -> None:
        base = RecommendationFeatures.actionable(property_id="property-1").with_scores(
            owner_motivation=100, economics=100
        )
        self.assertEqual(
            recommend(base.with_flags(is_synthetic=True)).action, "excluded"
        )
        self.assertEqual(
            recommend(base.with_flags(identity_verified=False)).action,
            "verify_identity",
        )
        self.assertEqual(
            recommend(
                base.with_flags(
                    violation_review_required=True,
                    critical_documents_missing=True,
                )
            ).action,
            "human_violation_review",
        )

    def test_contact_requires_specific_evidence_backed_opportunity(self) -> None:
        base = RecommendationFeatures.actionable(property_id="property-1")
        result = recommend(base.with_flags(specific_opportunity=False))
        self.assertEqual(result.action, "watch")


if __name__ == "__main__":
    unittest.main()
