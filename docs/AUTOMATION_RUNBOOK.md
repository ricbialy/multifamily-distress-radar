# Automation runbook

## Install and verify

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python -m compileall src
```

## Deterministic fixture demonstration

```bash
PYTHONPATH=src .venv/bin/python -m distress_radar fixture-demo \
  --matrix tests/fixtures/matrix_20.csv \
  --off-market tests/fixtures/off_market.csv \
  --output-dir exports/fixture-demo \
  --generated-at 2026-07-24T12:00:00+00:00
```

Review `recommendations.json`, `recommendations.csv`, and `daily_brief.md`.
The fixture command performs no network requests.

For authoritative address validation, first run `scrape-properties` and
`export-properties`, then add
`--county-properties exports/county-properties.csv` to the fixture command.
If that input is omitted or a folio is absent from it, the address remains
`corroborated` or `unverified`; do not manually promote it to `verified`.

## Local Matrix inbox

Place authorized `.csv` exports or saved `.eml` messages containing CSV
attachments in a private directory. `LocalMatrixInbox` extracts only CSV
attachments. Feed each payload to `MatrixCsvImporter.import_bytes`, then save
each listing through `IntelligenceStore.save_listing_snapshot`.

Do not store Matrix credentials. A future Gmail/IMAP adapter must read
credentials only from environment variables or an authorized connector.

## Existing live refresh

```bash
PYTHONPATH=src .venv/bin/python -m distress_radar refresh \
  --city hialeah_fl \
  --database data/radar.sqlite3 \
  --output-dir exports \
  --dashboard-json dashboard/src/data.json
```

Clerk collection is skipped without `MIAMI_DADE_CLERK_AUTH_KEY`. Tax and
contact research require authorized CSV exports. Never bypass CAPTCHA,
authentication, rate limits, or human verification.

## Failure handling

1. Record a `source_run` before collection.
2. Retry only transient timeout/network failures with bounded exponential
   backoff and jitter.
3. Persist raw responses before normalization when a response exists.
4. Mark authentication, blocking, schema, stale, or disabled states explicitly.
5. Include non-healthy sources in the daily brief.
6. Send no alert if no material listing/signal change occurred.

## Scheduling

Use the existing `scripts/refresh_hialeah.sh` for the legacy live flow. Schedule
Matrix/off-market imports only after placing an authorized export atomically in
the inbox. Keep source input and output directories outside public web roots.
