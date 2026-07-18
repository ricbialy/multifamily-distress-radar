#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATABASE="${RADAR_DATABASE:-$ROOT/data/hialeah.sqlite3}"
OUTPUT_DIR="${RADAR_OUTPUT_DIR:-$ROOT/exports}"
LOCK_DIR="${RADAR_LOCK_DIR:-$ROOT/data/refresh.lock}"
TAX_CSV="${RADAR_TAX_CSV:-$OUTPUT_DIR/delinquent_taxes.csv}"

mkdir -p "$(dirname "$DATABASE")" "$OUTPUT_DIR"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "A Hialeah refresh is already running; exiting without overlap." >&2
  exit 75
fi
trap 'rmdir "$LOCK_DIR"' EXIT

cd "$ROOT"

# Import an authorized tax export before scoring. The default path is optional;
# an explicitly configured path is treated as required to catch bad mounts or
# stale deployment configuration early.
if [[ -n "${RADAR_TAX_CSV:-}" && ! -r "$TAX_CSV" ]]; then
  echo "RADAR_TAX_CSV is not readable: $TAX_CSV" >&2
  exit 66
fi
if [[ -r "$TAX_CSV" ]]; then
  python -m distress_radar import-tax \
    --city hialeah_fl \
    --database "$DATABASE" \
    --input "$TAX_CSV"
else
  echo "No authorized delinquent-tax CSV found; continuing without tax import." >&2
fi

python -m distress_radar refresh \
  --city hialeah_fl \
  --database "$DATABASE" \
  --output-dir "$OUTPUT_DIR" \
  --dashboard-json "$ROOT/dashboard/src/data.json" \
  --top-limit "${RADAR_TOP_LIMIT:-50}" \
  --clerk-refresh-days "${CLERK_REFRESH_DAYS:-30}"

cd "$ROOT/dashboard"
npm run build
