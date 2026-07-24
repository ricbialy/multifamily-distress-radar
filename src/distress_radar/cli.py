from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from distress_radar.collectors import ArcGisPropertyCollector, MiamiDadeClerkCollector, TylerEnerGovCollector
from distress_radar.config import CityConfig, load_city_config
from distress_radar.storage import RadarStore
from distress_radar.pipeline import run_refresh
from distress_radar.tax_import import import_tax_csv
from distress_radar.alerts import deliver_webhook
from distress_radar.contact_import import import_contacts_csv
from distress_radar.orchestration.refresh import run_fixture_demo


def _collector(config: CityConfig) -> TylerEnerGovCollector:
    if not config.enabled:
        detail = f" {config.notes}" if config.notes else ""
        raise ValueError(f"City '{config.slug}' is disabled.{detail}")
    if config.source.adapter != "tyler_energov":
        raise ValueError(f"Unsupported adapter: {config.source.adapter}")
    return TylerEnerGovCollector(config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="distress-radar")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    statuses = subparsers.add_parser("statuses", help="List public case statuses")
    statuses.add_argument("--city", required=True)

    scrape = subparsers.add_parser("scrape", help="Collect code-enforcement cases")
    scrape.add_argument("--city", required=True)
    scrape.add_argument("--database", type=Path, default=Path("data/radar.sqlite3"))
    scrape.add_argument("--status", action="append", dest="statuses")
    scrape.add_argument("--max-pages", type=int)
    scrape.add_argument("--skip-details", action="store_true")
    scrape.add_argument("--include-violations", action="store_true")

    enrich = subparsers.add_parser(
        "enrich-top", help="Fetch full details for cases linked to top properties"
    )
    enrich.add_argument("--city", required=True)
    enrich.add_argument("--database", type=Path, default=Path("data/radar.sqlite3"))
    enrich.add_argument("--limit", type=int, default=25)
    enrich.add_argument("--skip-violations", action="store_true")

    properties = subparsers.add_parser(
        "scrape-properties", help="Collect the configured multifamily inventory"
    )
    properties.add_argument("--city", required=True)
    properties.add_argument("--database", type=Path, default=Path("data/radar.sqlite3"))
    properties.add_argument("--max-pages", type=int)

    export = subparsers.add_parser("export", help="Export normalized cases to CSV")
    export.add_argument("--city", required=True)
    export.add_argument("--database", type=Path, default=Path("data/radar.sqlite3"))
    export.add_argument("--output", type=Path, required=True)

    export_properties = subparsers.add_parser(
        "export-properties", help="Export the multifamily property universe"
    )
    export_properties.add_argument("--city", required=True)
    export_properties.add_argument(
        "--database", type=Path, default=Path("data/radar.sqlite3")
    )
    export_properties.add_argument("--output", type=Path, required=True)

    opportunities = subparsers.add_parser(
        "export-opportunities", help="Join properties to code cases and rank them"
    )
    opportunities.add_argument("--city", required=True)
    opportunities.add_argument("--database", type=Path, default=Path("data/radar.sqlite3"))
    opportunities.add_argument("--output", type=Path, required=True)
    opportunities.add_argument(
        "--limit", type=int, default=25, help="Use 0 to export the full inventory"
    )
    records = subparsers.add_parser(
        "scrape-official-records", help="Collect Clerk official records for top properties"
    )
    records.add_argument("--city", required=True)
    records.add_argument("--database", type=Path, default=Path("data/radar.sqlite3"))
    records.add_argument("--limit", type=int, default=25)
    records.add_argument("--refresh-days", type=int, default=30)
    records.add_argument("--force", action="store_true")

    dashboard = subparsers.add_parser("export-dashboard", help="Export bounded dashboard JSON")
    dashboard.add_argument("--city", required=True)
    dashboard.add_argument("--database", type=Path, default=Path("data/radar.sqlite3"))
    dashboard.add_argument("--output", type=Path, required=True)
    dashboard.add_argument("--limit", type=int, default=50)

    refresh = subparsers.add_parser("refresh", help="Run the resumable city refresh pipeline")
    refresh.add_argument("--city", required=True)
    refresh.add_argument("--database", type=Path, default=Path("data/radar.sqlite3"))
    refresh.add_argument("--output-dir", type=Path, default=Path("exports"))
    refresh.add_argument("--dashboard-json", type=Path, default=Path("dashboard/src/data.json"))
    refresh.add_argument("--top-limit", type=int, default=50)
    refresh.add_argument("--clerk-refresh-days", type=int, default=30)

    tax_import = subparsers.add_parser("import-tax", help="Import an authorized delinquent-tax CSV")
    tax_import.add_argument("--city", required=True)
    tax_import.add_argument("--database", type=Path, default=Path("data/radar.sqlite3"))
    tax_import.add_argument("--input", type=Path, required=True)
    alerts = subparsers.add_parser("deliver-alerts", help="Deliver pending alerts to a configured webhook")
    alerts.add_argument("--city", required=True)
    alerts.add_argument("--database", type=Path, default=Path("data/radar.sqlite3"))
    alerts.add_argument("--limit", type=int, default=100)
    lead = subparsers.add_parser("set-lead", help="Create or update acquisition workflow state")
    lead.add_argument("--city", required=True)
    lead.add_argument("--database", type=Path, default=Path("data/radar.sqlite3"))
    lead.add_argument("--folio", required=True)
    lead.add_argument("--stage", required=True)
    lead.add_argument("--assignee")
    lead.add_argument("--follow-up")
    lead.add_argument("--disposition")
    lead.add_argument("--notes")
    export_leads = subparsers.add_parser("export-leads", help="Export acquisition workflow CSV")
    export_leads.add_argument("--city", required=True)
    export_leads.add_argument("--database", type=Path, default=Path("data/radar.sqlite3"))
    export_leads.add_argument("--output", type=Path, required=True)
    contacts = subparsers.add_parser("import-contacts", help="Import authorized contact research CSV")
    contacts.add_argument("--city", required=True)
    contacts.add_argument("--database", type=Path, default=Path("data/radar.sqlite3"))
    contacts.add_argument("--input", type=Path, required=True)
    fixture_demo = subparsers.add_parser(
        "fixture-demo",
        help="Run the combined acquisition-intelligence flow against authorized fixtures",
    )
    fixture_demo.add_argument("--matrix", type=Path, required=True)
    fixture_demo.add_argument("--off-market", type=Path, required=True)
    fixture_demo.add_argument(
        "--county-properties",
        type=Path,
        help=(
            "Optional export-properties CSV from Miami-Dade Property Point View; "
            "required for authoritative address verification"
        ),
    )
    fixture_demo.add_argument("--output-dir", type=Path, required=True)
    fixture_demo.add_argument(
        "--generated-at",
        default=None,
        help="ISO-8601 timestamp for deterministic fixture output",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "fixture-demo":
            from distress_radar.sources.public.property_csv import (
                PropertyRecordCsvImporter,
            )

            property_records = (
                PropertyRecordCsvImporter().import_file(args.county_properties)
                if args.county_properties
                else ()
            )
            result = run_fixture_demo(
                matrix_path=args.matrix,
                off_market_path=args.off_market,
                output_dir=args.output_dir,
                generated_at=args.generated_at
                or datetime.now(timezone.utc).isoformat(),
                property_records=property_records,
            )
            print(
                json.dumps(
                    {
                        "canonical_property_count": result.canonical_property_count,
                        "json_path": str(result.json_path),
                        "csv_path": str(result.csv_path),
                        "brief_path": str(result.brief_path),
                    },
                    indent=2,
                )
            )
            return

        config = load_city_config(args.city)
        if args.command == "statuses":
            for status in _collector(config).list_statuses():
                print(status)
            return

        if args.command == "export":
            with RadarStore(args.database) as store:
                count = store.export_csv(config.slug, args.output)
            print(f"Exported {count} records to {args.output}")
            return

        if args.command == "export-properties":
            with RadarStore(args.database) as store:
                count = store.export_properties_csv(config.slug, args.output)
            print(f"Exported {count} properties to {args.output}")
            return

        if args.command == "export-opportunities":
            with RadarStore(args.database) as store:
                count = store.export_opportunities_csv(
                    config.slug, args.output, None if args.limit == 0 else args.limit
                )
            print(f"Exported {count} ranked properties to {args.output}")
            return

        if args.command == "export-dashboard":
            with RadarStore(args.database) as store:
                count = store.export_dashboard_json(config.slug, args.output, args.limit)
            print(f"Exported {count} dashboard properties to {args.output}")
            return

        if args.command == "refresh":
            summary = run_refresh(
                config.slug, args.database, args.output_dir, args.dashboard_json,
                args.top_limit, args.clerk_refresh_days,
            )
            print(json.dumps(summary, indent=2))
            return

        if args.command == "import-tax":
            records = import_tax_csv(config.slug, args.input)
            with RadarStore(args.database) as store:
                run_id = store.start_run(config.slug, "miami_dade_tax_csv", (args.input.name,))
                stats = store.upsert_tax_delinquencies(run_id, records)
                store.finish_run(run_id, "completed", len(records))
            print(f"Imported {len(records)} tax rows (new={stats.new}, changed={stats.changed}, unchanged={stats.unchanged}); run_id={run_id}")
            return

        if args.command == "deliver-alerts":
            url = os.environ.get("RADAR_ALERT_WEBHOOK_URL")
            if not url:
                raise ValueError("RADAR_ALERT_WEBHOOK_URL is not set")
            with RadarStore(args.database) as store:
                alerts = store.pending_alerts(config.slug, args.limit)
                if not alerts:
                    print("No pending alerts")
                    return
                receipt = deliver_webhook(url, alerts, os.environ.get("RADAR_ALERT_TOKEN"))
                store.mark_alerts_delivered([int(alert["id"]) for alert in alerts])
            print(json.dumps({"delivered": len(alerts), "receipt": receipt}))
            return

        if args.command == "set-lead":
            with RadarStore(args.database) as store:
                lead = store.set_property_lead(
                    config.slug, args.folio, stage=args.stage, assignee=args.assignee,
                    next_follow_up_date=args.follow_up, disposition=args.disposition, notes=args.notes,
                )
            print(json.dumps(lead, indent=2))
            return

        if args.command == "export-leads":
            with RadarStore(args.database) as store:
                count = store.export_leads_csv(config.slug, args.output)
            print(f"Exported {count} acquisition leads to {args.output}")
            return

        if args.command == "import-contacts":
            records = import_contacts_csv(config.slug, args.input)
            with RadarStore(args.database) as store:
                stats = store.upsert_property_contacts(records)
            print(f"Imported {len(records)} contacts (new={stats.new}, changed={stats.changed}, unchanged={stats.unchanged})")
            return

        if args.command == "scrape-properties":
            if not config.enabled:
                raise ValueError(f"City '{config.slug}' is disabled")
            if config.property_source is None:
                raise ValueError(f"City '{config.slug}' has no configured property source")
            collector = ArcGisPropertyCollector(config)
            filter_description = (
                f"units:{config.property_source.min_units}-{config.property_source.max_units}"
            )
            with RadarStore(args.database) as store:
                run_id = store.start_run(
                    config.slug, config.property_source.name, (filter_description,)
                )
                try:
                    result = collector.collect(max_pages=args.max_pages)
                    store.save_raw_documents(run_id, result.raw_documents)
                    stats = store.upsert_properties(run_id, result.records)
                    store.finish_run(run_id, "completed", len(result.records))
                except Exception as exc:
                    store.finish_run(run_id, "failed", 0, str(exc))
                    raise
            print(
                f"Collected {len(result.records)} properties "
                f"(new={stats.new}, changed={stats.changed}, "
                f"unchanged={stats.unchanged}); run_id={run_id}"
            )
            return

        if args.command == "scrape-official-records":
            source = config.official_records_source
            assert source is not None
            with RadarStore(args.database) as store:
                folios = store.top_property_folios(config.slug, args.limit)
                if not args.force:
                    folios = store.folios_needing_official_records(
                        config.slug, folios, args.refresh_days
                    )
                if not folios:
                    print("All selected folios have fresh cached Clerk results; no API units used")
                    return
                collector = MiamiDadeClerkCollector(config)
                run_id = store.start_run(config.slug, source.name, (f"top:{args.limit}",))
                try:
                    result = collector.collect(folios)
                    store.save_raw_documents(run_id, result.raw_documents)
                    stats = store.upsert_official_records(run_id, result.records)
                    store.finish_run(run_id, "completed", len(result.records))
                except Exception as exc:
                    store.finish_run(run_id, "failed", 0, str(exc))
                    raise
            print(f"Collected {len(result.records)} official records for {len(folios)} properties (new={stats.new}, changed={stats.changed}, unchanged={stats.unchanged}); run_id={run_id}")
            return

        if args.command == "enrich-top":
            collector = _collector(config)
            with RadarStore(args.database) as store:
                folios = store.top_property_folios(config.slug, args.limit)
                records = store.cases_for_folios(
                    config.slug, folios, only_missing_details=True
                )
                if not records:
                    print(
                        f"Top {len(folios)} properties are already fully detail-enriched"
                    )
                    return
                run_id = store.start_run(
                    config.slug,
                    config.source.name,
                    (f"enrichment:top-{args.limit}",),
                )
                try:
                    result = collector.enrich_records(
                        records, include_violations=not args.skip_violations
                    )
                    store.save_raw_documents(run_id, result.raw_documents)
                    stats = store.upsert_cases(run_id, result.records)
                    store.finish_run(run_id, "completed", len(result.records))
                except Exception as exc:
                    store.finish_run(run_id, "failed", 0, str(exc))
                    raise
            print(
                f"Enriched {len(result.records)} cases across {len(folios)} properties "
                f"(changed={stats.changed}, unchanged={stats.unchanged}); run_id={run_id}"
            )
            return

        statuses = tuple(args.statuses or config.source.active_statuses)
        if not statuses:
            raise ValueError("No statuses selected or configured")
        collector = _collector(config)
        with RadarStore(args.database) as store:
            run_id = store.start_run(config.slug, config.source.name, statuses)
            try:
                result = collector.collect(
                    statuses,
                    max_pages=args.max_pages,
                    fetch_details=not args.skip_details,
                    include_violations=args.include_violations,
                )
                store.save_raw_documents(run_id, result.raw_documents)
                stats = store.upsert_cases(run_id, result.records)
                store.finish_run(run_id, "completed", len(result.records))
            except Exception as exc:
                store.finish_run(run_id, "failed", 0, str(exc))
                raise
        print(
            f"Collected {len(result.records)} cases "
            f"(new={stats.new}, changed={stats.changed}, unchanged={stats.unchanged}); "
            f"run_id={run_id}"
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
