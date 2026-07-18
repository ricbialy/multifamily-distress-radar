from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def run_refresh(
    city: str,
    database: Path,
    output_dir: Path,
    dashboard_json: Path,
    top_limit: int,
    clerk_refresh_days: int,
) -> dict[str, object]:
    started = datetime.now(timezone.utc).isoformat()
    stages: list[dict[str, object]] = []

    def run(name: str, args: list[str], *, optional: bool = False) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "distress_radar", *args],
            text=True,
            capture_output=True,
        )
        stage = {
            "name": name,
            "status": "completed" if completed.returncode == 0 else "failed",
            "output": completed.stdout.strip(),
            "error": completed.stderr.strip() or None,
        }
        stages.append(stage)
        if completed.returncode and not optional:
            raise RuntimeError(f"refresh stage '{name}' failed: {completed.stderr.strip()}")

    run("properties", ["scrape-properties", "--city", city, "--database", str(database)])
    run("code_cases", ["scrape", "--city", city, "--database", str(database), "--skip-details"])
    run("enrich_top", ["enrich-top", "--city", city, "--database", str(database), "--limit", str(top_limit)])
    if os.environ.get("MIAMI_DADE_CLERK_AUTH_KEY"):
        run("official_records", ["scrape-official-records", "--city", city, "--database", str(database), "--limit", str(top_limit), "--refresh-days", str(clerk_refresh_days)])
    else:
        stages.append({"name": "official_records", "status": "skipped", "output": "MIAMI_DADE_CLERK_AUTH_KEY is not set", "error": None})

    output_dir.mkdir(parents=True, exist_ok=True)
    run("cases_export", ["export", "--city", city, "--database", str(database), "--output", str(output_dir / f"{city}_open_cases.csv")])
    run("properties_export", ["export-properties", "--city", city, "--database", str(database), "--output", str(output_dir / f"{city}_properties.csv")])
    run("ranking_export", ["export-opportunities", "--city", city, "--database", str(database), "--output", str(output_dir / f"{city}_top_{top_limit}.csv"), "--limit", str(top_limit)])
    run("dashboard_export", ["export-dashboard", "--city", city, "--database", str(database), "--output", str(dashboard_json), "--limit", str(top_limit)])
    if os.environ.get("RADAR_ALERT_WEBHOOK_URL"):
        run("alert_delivery", ["deliver-alerts", "--city", city, "--database", str(database)], optional=True)
    else:
        stages.append({"name": "alert_delivery", "status": "skipped", "output": "RADAR_ALERT_WEBHOOK_URL is not set", "error": None})

    ingest_url = os.environ.get("RADAR_INGEST_URL")
    ingest_token = os.environ.get("RADAR_INGEST_TOKEN")
    sites_bypass_token = os.environ.get("RADAR_SITES_BYPASS_TOKEN")
    if ingest_url and ingest_token:
        properties = json.loads(dashboard_json.read_text(encoding="utf-8"))
        body = json.dumps({
            "city": city,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "properties": properties,
            "source": "scheduled-refresh",
        }).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {ingest_token}",
            "Content-Type": "application/json",
            "User-Agent": "multifamily-distress-radar/0.7",
        }
        if sites_bypass_token:
            headers["OAI-Sites-Authorization"] = f"Bearer {sites_bypass_token}"
        request = urllib.request.Request(
            ingest_url,
            data=body,
            method="PUT",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                receipt = json.loads(response.read().decode("utf-8"))
            stages.append({"name": "publish_snapshot", "status": "completed", "output": receipt, "error": None})
        except Exception as exc:
            stages.append({"name": "publish_snapshot", "status": "failed", "output": None, "error": str(exc)})
            raise RuntimeError("dashboard snapshot publication failed") from exc
    else:
        stages.append({"name": "publish_snapshot", "status": "skipped", "output": "RADAR_INGEST_URL or RADAR_INGEST_TOKEN is not set", "error": None})

    summary = {
        "city": city,
        "started_at": started,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "stages": stages,
    }
    (output_dir / "refresh_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary
