# Multifamily Distress Radar — acquisition intelligence

Version 0.15 adds a tested acquisition-intelligence foundation on top of the
existing public-record collectors. It has two fixture-capable discovery paths:
authorized Matrix CSV/email exports and authorized off-market CSV exports.
Both paths feed canonical identity, evidence, underwriting, recommendation,
outcome-capture, and reporting modules.

This is not an autonomous acquisition system and it does not have an active ML
model. Live Tyler EnerGov and Miami-Dade property collection remain available
through the legacy refresh flow. Matrix is an authorized export workflow;
off-market fixture data is a manual import; paid vendor adapters are disabled
unless credentials exist and their live API implementations are still pending.

The checkout used to build this branch started from GitHub `main` at version
0.14. The previously described uncommitted version 0.15 was not present on
GitHub and could not be recovered, so its functionality was rebuilt from the
published v0.14 baseline.

## Acquisition-intelligence fixture demo

## REAL-PILOT-02 corrective acquisition-review workflow

Run the genuine Matrix export through row validation, county identity
verification, live Hialeah public-record enrichment, persisted hard gates, and
database-derived reports:

```bash
PYTHONPATH=src .venv/bin/python -m distress_radar pilot-run \
  --matrix /absolute/path/to/Agent\ Single\ Line\ -\ COM.csv \
  --db /absolute/path/to/pilot.sqlite \
  --output-dir /absolute/path/to/pilot-output \
  --municipality hialeah \
  --minimum-acceptable-cap-rate 0.04 \
  --target-cap-rate 0.06
```

Cap-rate criteria use decimal units and must be supplied together. If omitted,
the run records `criteria_not_configured`; supported NOI may be displayed, but
cap rate earns no credit and economics alone cannot qualify a property.

The genuine export headers map as follows: `MLS # Link` → MLS number, `St` →
listing status, `Address` → submitted street address, `Current Price` → list
price, and `Type of Property` → property class. Missing optional city, folio,
unit, rent, NOI, and expense columns remain unknown; they are not filled from
assumptions. Per-row acceptance or rejection is written to `import_ledger.csv`.

For a fresh database, the repeatable G0–G11 acceptance command is:

```bash
PYTHONPATH=src .venv/bin/python -m distress_radar pilot-verify \
  --matrix /absolute/path/to/Agent\ Single\ Line\ -\ COM.csv \
  --db /absolute/path/to/fresh-verification.sqlite \
  --output-dir /absolute/path/to/verification-output \
  --municipality hialeah
```

Neither command modifies the supplied Matrix file. The verification command
creates a separate controlled-change copy under its output directory.

The reports keep acquisition attractiveness and municipal-review urgency as
separate rankings. `qualified_queue.json` remains as a compatibility alias for
`acquisition_queue.json`; MLS examples are written separately and are never
forced into either numerical top ten. Source-health warnings and opportunity
changes are also emitted separately.

Record an analyst disposition against the latest reviewed material-content
hash with:

```bash
PYTHONPATH=src .venv/bin/python -m distress_radar pilot-disposition \
  --db /absolute/path/to/pilot.sqlite \
  --property-id property-identifier \
  --disposition dismiss \
  --notes "Reviewed by analyst"
```

Dismissals reopen when material source evidence changes. Contact approval is
also hash-bound and cannot bypass identity, scope, municipal-risk, or
underwriting hard gates.

A new `watch` additionally requires a specific reason, property-matched
evidence IDs, a bounded trigger with a structured condition, an explicit next
action, and a creator. Scheduled watches require a future recheck timestamp;
event watches require an exact source and evidence or signal class. Incomplete
legacy rows remain visible as `legacy_incomplete_watch` and do not run
automatically.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .

PYTHONPATH=src .venv/bin/python -m distress_radar fixture-demo \
  --matrix tests/fixtures/matrix_20.csv \
  --off-market tests/fixtures/off_market.csv \
  --output-dir exports/fixture-demo \
  --generated-at 2026-07-24T12:00:00+00:00
```

The demo creates:

- `recommendations.json` with evidence-backed fields and separate scores.
- `recommendations.csv` for analysis.
- `daily_brief.md` with actionable opportunities, diligence gaps, and source
  warnings.

Addresses are not labeled `verified` from an MLS or spreadsheet alone. To
validate them against Miami-Dade Property Point View by exact folio, collect
and export the public county records, then pass that export into the combined
flow:

```bash
PYTHONPATH=src .venv/bin/python -m distress_radar scrape-properties \
  --city hialeah_fl --database data/radar.sqlite3
PYTHONPATH=src .venv/bin/python -m distress_radar export-properties \
  --city hialeah_fl --database data/radar.sqlite3 \
  --output exports/county-properties.csv
PYTHONPATH=src .venv/bin/python -m distress_radar fixture-demo \
  --matrix tests/fixtures/matrix_20.csv \
  --off-market tests/fixtures/off_market.csv \
  --county-properties exports/county-properties.csv \
  --output-dir exports/fixture-demo
```

The brief distinguishes `verified`, `mismatch`, `corroborated`, and
`unverified`. A mismatch displays the county site address and retains the
submitted address in the validation details for review.

No credential is used by the fixture demo. See
[`docs/AUTOMATION_RUNBOOK.md`](docs/AUTOMATION_RUNBOOK.md) for live and
fixture workflows.

## Evidence boundary

Every new intelligence field is modeled as reported, calculated, inferred, or
unknown, with source, source record, timestamp, confidence, and freshness.
Missing data and source failures remain explicit. They are never converted to
claims such as “no lien,” “no violation,” or “clean property.”

## Legacy collector history

Version 0.14 adds authorized contact-research CSV import with source,
verification status, and confidence. Version 0.13 added a persistent acquisition workflow with lead stages, assignee,
follow-up date, disposition, notes, dashboard export, and CRM CSV export.
Version 0.12 added conservative owner-name normalization, target portfolio
property/unit totals, and related-property evidence. Version 0.11 added durable,
retry-safe webhook alerts for new lis pendens,
liens, delinquent taxes, and significant code-enforcement changes. Version 0.10
added deduplicated Clerk instrument lifecycle events with
first-seen, last-seen, active, and resolved state. It also supports unattended
import of an authorized delinquent-tax CSV before each scheduled refresh and a
separate acquisition-opportunity score with a compliant,
optional Miami-Dade Clerk Official Records API connector. The connector is
disabled by default because the Clerk requires an enabled developer account
and purchased API units. It never stores the API key in configuration, raw
documents, or source URLs.

The priority score is `verified code-enforcement distress + property
opportunity`. Opportunity uses ownership duration, building age, absentee
mailing, and target-portfolio size. These are screening indicators—not claims
of financial distress.

This repository implements the ingestion and first ranking layer described in
the project specification. Hialeah and Surfside use live-tested Tyler EnerGov
code-enforcement collection. Hialeah also uses Miami-Dade County's authoritative
weekly Property Point View to build its 10–80-unit multifamily universe.

## What works

- Discovers the portal tenant instead of hard-coding tenant headers.
- Resolves human-readable case statuses to portal IDs.
- Paginates public code-enforcement searches.
- Optionally fetches case details and structured violation rows.
- Stores every raw response with source URL and collection timestamp.
- Normalizes current code cases into SQLite.
- Deduplicates records and logs new/changed versions by content hash.
- Exports a clean CSV for analysis.
- Keeps city-specific URLs and active-status rules in YAML.
- Collects the Hialeah 10–80-unit multifamily parcel universe.
- Joins open cases to properties by normalized folio and exports a transparent
  code-enforcement ranking.

No login, CAPTCHA bypass, or private endpoint is used. Keep request rates low
and review each jurisdiction's terms before running on a schedule.

After obtaining Clerk developer access, enable `official_records_source` in
the Hialeah configuration, set `MIAMI_DADE_CLERK_AUTH_KEY` in the environment,
and run `distress-radar scrape-official-records --city hialeah_fl --limit 25`.
Successful folio responses are cached for 30 days by default. Use
`--refresh-days N` to change the interval or `--force` for an intentional paid
refresh. Existing mortgages are informational and do not increase financial
distress; only unmatched explicit lien or lis-pendens codes do.
The tax-report vendor currently presents human verification, so this project
does not automate around that control; use a licensed/API feed or a manually
exported CSV instead.

## Dashboard

`dashboard/` is a responsive React/Vite evidence explorer generated from the
live Hialeah snapshot. It provides ranked-property search and filtering,
score decomposition, case evidence, grouped Clerk instruments, and source
links. Run `cd dashboard && npm install && npm run dev`, or deploy `dashboard/dist`.

## Scheduled refresh

`distress-radar refresh` orchestrates property inventory, code cases, targeted
detail enrichment, cache-aware Clerk collection, CSV exports, dashboard JSON,
and a machine-readable `refresh_summary.json`. Clerk is skipped safely when its
environment credential is absent.

```bash
distress-radar refresh --city hialeah_fl \
  --database data/radar.sqlite3 --top-limit 50 \
  --output-dir exports --dashboard-json dashboard/src/data.json
```

For unattended execution, `scripts/refresh_hialeah.sh` prevents overlapping
runs, honors `RADAR_DATABASE`, `RADAR_OUTPUT_DIR`, `RADAR_TOP_LIMIT`,
`RADAR_TAX_CSV`, and `CLERK_REFRESH_DAYS`, and rebuilds the dashboard after a
successful refresh. If `RADAR_TAX_CSV` is set, it must name a readable,
authorized CSV and the run fails early otherwise. When it is unset, the script
automatically imports `exports/delinquent_taxes.csv` when that file exists and
otherwise continues without tax data.
Schedule that script on the fixed-egress host that is authorized by the Clerk.
When `RADAR_INGEST_URL` and `RADAR_INGEST_TOKEN` are set, the final bounded JSON
snapshot is sent to the published dashboard only after every required refresh
and export stage succeeds. The token is sent in an Authorization header and is
never written to the refresh summary.
Private hosted apps also require `RADAR_SITES_BYPASS_TOKEN`, which is sent only
in the platform authorization header and is likewise excluded from logs.

## Change alerts

Set `RADAR_ALERT_WEBHOOK_URL` to a Slack/Teams automation, Zapier/Make hook, or
your own receiver. `RADAR_ALERT_TOKEN` optionally adds bearer authentication.
Alerts are persisted before delivery, deduplicated by source revision, and
marked delivered only after a successful webhook response. A notification
failure does not block collection and is retried on the next refresh.

## Acquisition workflow

```bash
distress-radar set-lead --city hialeah_fl --folio 0123456789012 \
  --stage qualified --assignee ricardo --follow-up 2026-08-01 \
  --notes "Confirm ownership and call broker"
distress-radar export-leads --city hialeah_fl \
  --output exports/hialeah_leads.csv
```

Stages are `new`, `researching`, `qualified`, `contacted`, `negotiating`,
`won`, `lost`, and `paused`.

Contact research must come from an authorized business/public-record source.
Import columns such as folio, contact name, role, email, phone, mailing address,
verification status, confidence (0–1), and source URL:

```bash
distress-radar import-contacts --city hialeah_fl \
  --input exports/authorized_contacts.csv
```

## Delinquent-tax import

Use an authorized Miami-Dade report export rather than automating around its
human-verification page. The importer accepts common aliases for folio, tax
year, balance due, status, certificate, owner, and address:

```bash
distress-radar import-tax --city hialeah_fl \
  --database data/hialeah.sqlite3 --input exports/delinquent_taxes.csv
```

Verified unpaid tax rows add a separate 35-point financial signal plus a
capped multi-year bonus. Paid rows do not score.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .

# See the statuses exposed by Surfside.
distress-radar statuses --city surfside_fl

# Fast proof: one page, search results only.
distress-radar scrape --city surfside_fl --max-pages 1 --skip-details

# Production-oriented active-case collection, including case descriptions.
distress-radar scrape --city surfside_fl

# Build Hialeah's property backbone and collect all configured active cases.
distress-radar scrape-properties --city hialeah_fl
distress-radar scrape --city hialeah_fl --skip-details

# Enrich only the cases driving the current top 25 with descriptions and
# structured violation rows.
distress-radar enrich-top --city hialeah_fl --limit 25

# Export the property universe and the first ranked top 25.
distress-radar export-properties --city hialeah_fl --output data/hialeah_properties.csv
distress-radar export-opportunities --city hialeah_fl --output data/hialeah_top_25.csv

# Also request structured violation rows for every case.
distress-radar scrape --city surfside_fl --include-violations

# Export the latest normalized records.
distress-radar export --city surfside_fl --output data/surfside_cases.csv
```

The default database is `data/radar.sqlite3`. Raw JSON responses are stored in
the `raw_documents` table, while `code_cases` holds current normalized state and
`case_changes` is the append-only change log.

Run the dependency-free test suite with:

```bash
python -m unittest discover -s tests -v
```

## City configuration

Add a YAML file under `config/cities/`. The implemented adapter is
`tyler_energov`; other municipal systems should become separate adapters rather
than city-specific conditionals in this one.

`active_statuses` is a research policy, not a universal legal definition of an
open violation. Surfside's configuration includes unresolved and enforcement
statuses and excludes closed/canceled statuses. Adjust it as the acquisition
strategy develops.

## Ranking boundary

`code_enforcement_score` is deliberately narrow and explainable: liens receive
the highest weight, followed by special-master/hearing stages, notices,
re-inspections/appeals, and warnings, with a capped repeat-case bonus. It is not
the final distress or opportunity score and should not be presented as one.

## Important MVP gaps

This now creates the Hialeah 10–80-unit property universe and a code-enforcement
ranking with optional imported tax delinquency. It does not yet add eviction,
utility, vacancy, or permit signals; resolve related LLCs; verify contacts;
calculate separate full distress/opportunity scores; or operate a CRM. Those
layers should consume the stable normalized store rather than adding logic to
the collectors.
