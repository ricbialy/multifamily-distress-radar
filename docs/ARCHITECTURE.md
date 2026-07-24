# Architecture

## Scope

The system has two discovery scouts and one deterministic intelligence path.
The production baseline is explainable rules; ML remains off until real,
point-in-time outcome labels support a valid comparison.

```text
Matrix CSV / local .eml inbox ─┐
                               ├─ identity ─ evidence ─ underwriting
Authorized off-market CSV ─────┘                      ├─ recommendations
Existing public collectors ──────────────────────────┤
                                                     └─ JSON / CSV / daily brief
```

## Modules

- `domain/`: immutable property, owner, listing, signal, and evidence values.
- `identity/`: folio-first, normalized-address, owner-alias, and review-only
  fuzzy matching.
- `sources/mls/`: Matrix CSV validation, parsing, email-attachment extraction,
  snapshot comparison, and cross-class grouping.
- `sources/public/`: authorized off-market import. Existing Tyler EnerGov,
  Miami-Dade Property Point View, Clerk, and tax importers remain under
  `collectors/` while they are incrementally migrated.
- `sources/vendors/`: credential-gated interfaces. They never return invented
  live data.
- `orchestration/`: pagination, bounded exponential retry with jitter, and the
  combined fixture workflow.
- `underwriting/`: separate 2–4-unit and 5+-unit models plus preliminary offer
  deductions.
- `recommendations/`: independent score dimensions and deterministic actions.
- `ml/`: point-in-time datasets, chronological split, and label-readiness gate.
- `reports/`: stable JSON/CSV schema and daily brief.
- `intelligence_store.py`: normalized SQLite repository separate from the
  legacy `storage.py`.

## Identity guarantees

Automatic confirmation uses, in order:

1. Exact normalized folio/APN.
2. Exact normalized address plus municipality.

Fuzzy address similarity can only create a review candidate. It cannot confirm
a property. Owner aliases remove conservative legal suffixes, but an alias
match never asserts beneficial ownership.

## Failure semantics

Collection health is one of `healthy`, `degraded`, `blocked`,
`authentication_required`, `schema_changed`, `stale`, or `disabled`.
Non-healthy source runs persist as coverage warnings. An unavailable source
does not contribute a zero-risk or clean-property fact.

## Deployment boundary

SQLite is the supported first production-test database. Repository methods and
normalized tables isolate SQL from the domain modules so PostgreSQL can be
added later. Raw payload and listing snapshot history remain append-only for
audit.
