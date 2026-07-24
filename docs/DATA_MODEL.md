# Data model

## Canonical identity

- `canonical_properties`: canonical property ID, folio, normalized location,
  municipality, jurisdiction, and optional coordinates.
- `canonical_owners`: entity/person display identity.
- `owner_aliases`: evidence-scoped names linked to a canonical owner.
- `property_ownership`: dated, sourced owner/property relationships.

## Source and evidence

- `source_runs`: attempt, completion, state, counts, and error.
- `source_health`: last attempt, last success, current state, and coverage
  warning.
- `source_records`: source record plus raw and normalized payload.
- `evidence_items`: property field, JSON value, source name/record/URL,
  collection time, freshness, confidence, and value type.
- `listing_snapshots`: append-only normalized and raw MLS snapshots.
- `listing_changes`: append-only material listing events.
- `property_signals`: dated signal with evidence IDs and status.

An unknown evidence value is stored as JSON `null` with `value_type=unknown`
and metadata explaining why. It is not stored as `false` or zero.

## Decisions and outcomes

- `underwriting_runs`: point-in-time inputs and outputs by model.
- `recommendations`: action, separate scores, and explanation.
- `human_decisions`: review, contact, and diligence decisions.
- `acquisition_outcomes`: contact, offer, acceptance, close/loss, price, cost,
  and outcome metadata.
- `ml_models`: version, target, metrics, model card, and inactive/active flag.
- `ml_predictions`: point-in-time feature snapshot and explanation.

## Legacy compatibility

The existing `RadarStore` tables remain unchanged for live city collection,
case history, taxes, alerts, leads, and contacts. `IntelligenceStore` can use
the same SQLite file because all new migrations are additive and
`CREATE TABLE IF NOT EXISTS`.
