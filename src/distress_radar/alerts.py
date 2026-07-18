from __future__ import annotations

import json
import urllib.request
from typing import Any


def deliver_webhook(url: str, alerts: list[dict[str, Any]], token: str | None = None) -> dict[str, Any]:
    body = json.dumps({"event": "distress_radar.alerts", "alerts": alerts}).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "multifamily-distress-radar/0.11"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        response_body = response.read().decode("utf-8")
        return {"status": response.status, "body": response_body[:1000]}
