#!/usr/bin/env python3
"""
Taunton EV charger monitor — polls operators' open-data feeds and logs the
status of every charging bay (EVSE) within a set radius of Taunton.

Each run appends one row per EVSE to data/status/YYYY-MM-DD.csv, refreshes
data/sites.csv (reference data), and appends a health line per feed to
data/poll_log.csv. Designed to run every 5 minutes from GitHub Actions.

Usage:
    python poller.py               # poll and write data
    python poller.py --dry-run     # poll and print a summary, write nothing
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
STATUS_DIR = DATA / "status"
MAX_PAGES = 200
TIMEOUT = 60
USER_AGENT = "taunton-ev-monitor/1.0 (research; open data under PCPR 2023)"

STATUS_FIELDS = [
    "polled_at_utc", "operator", "location_id", "location_name", "evse_uid",
    "status", "max_kw", "status_updated_utc",
]
SITE_FIELDS = [
    "operator", "location_id", "location_name", "address", "postcode",
    "lat", "lon", "distance_miles", "evse_uid", "evse_id", "max_kw",
    "connectors", "last_seen_utc",
]
LOG_FIELDS = [
    "polled_at_utc", "operator", "ok", "locations_total",
    "locations_in_radius", "evses", "error",
]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def miles_between(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _get(url: str, headers: dict) -> tuple[object, dict]:
    """Fetch JSON. Returns (body, lower-cased response headers)."""
    if url.startswith("file://"):
        with open(url[len("file://"):], encoding="utf-8") as fh:
            return json.load(fh), {}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        return body, {k.lower(): v for k, v in resp.headers.items()}


def _next_link(headers: dict) -> str | None:
    """OCPI paginates with an RFC 5988 Link header: <url>; rel="next"."""
    link = headers.get("link")
    if not link:
        return None
    for part in link.split(","):
        if 'rel="next"' in part or "rel=next" in part:
            start, end = part.find("<"), part.find(">")
            if start != -1 and end > start:
                return part[start + 1:end].strip()
    return None


def _unwrap(body) -> list:
    """Accept an OCPI envelope ({"data": [...]}), a bare list, or common variants."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("data", "locations", "items", "results"):
            val = body.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):          # e.g. {"data": {"locations": [...]}}
                inner = _unwrap(val)
                if inner:
                    return inner
    return []


def fetch_locations(feed: dict) -> list:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    token_env = feed.get("token_env")
    if token_env:
        token = os.environ.get(token_env, "").strip()
        if not token:
            raise RuntimeError(f"secret {token_env} is not set")
        scheme = feed.get("auth_scheme", "Token")
        headers["Authorization"] = f"{scheme} {token}".strip()

    url, out, pages = feed["url"], [], 0
    while url and pages < MAX_PAGES:
        body, resp_headers = _get(url, headers)
        out.extend(_unwrap(body))
        url = _next_link(resp_headers)
        pages += 1
    return out


def _coords(loc: dict) -> tuple[float, float] | None:
    c = loc.get("coordinates") or {}
    lat = c.get("latitude", loc.get("latitude"))
    lon = c.get("longitude", loc.get("longitude"))
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def _connector_kw(con: dict) -> float | None:
    w = con.get("max_electric_power")
    if w:
        return round(float(w) / 1000, 1)
    v, a = con.get("max_voltage"), con.get("max_amperage")
    if v and a:
        phases = 3 if str(con.get("power_type", "")).startswith("AC_3") else 1
        return round(float(v) * float(a) * phases / 1000, 1)
    return None


def _evse_kw(evse: dict) -> float | None:
    kws = [k for k in (_connector_kw(c) for c in evse.get("connectors") or []) if k]
    return max(kws) if kws else None


def _append_csv(path: Path, fields: list, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


# --------------------------------------------------------------------------
# Main poll
# --------------------------------------------------------------------------
def poll(config: dict, dry_run: bool = False) -> int:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = now.isoformat().replace("+00:00", "Z")
    clat, clon = config["centre"]["lat"], config["centre"]["lon"]
    radius = float(config.get("radius_miles", 3))

    status_rows, site_rows, log_rows = [], [], []

    for feed in config.get("feeds", []):
        if not feed.get("enabled"):
            continue
        name = feed["name"]
        log = {"polled_at_utc": stamp, "operator": name, "ok": 0,
               "locations_total": 0, "locations_in_radius": 0, "evses": 0, "error": ""}
        try:
            locations = fetch_locations(feed)
            log["locations_total"] = len(locations)
            for loc in locations:
                pt = _coords(loc)
                if not pt:
                    continue
                dist = miles_between(clat, clon, *pt)
                if dist > radius:
                    continue
                log["locations_in_radius"] += 1
                if loc.get("publish") is False:
                    continue
                loc_id = loc.get("id", "")
                loc_name = loc.get("name") or loc.get("address", "")
                for evse in loc.get("evses") or []:
                    uid = evse.get("uid") or evse.get("evse_id", "")
                    kw = _evse_kw(evse)
                    status_rows.append({
                        "polled_at_utc": stamp, "operator": name,
                        "location_id": loc_id, "location_name": loc_name,
                        "evse_uid": uid, "status": evse.get("status", "UNKNOWN"),
                        "max_kw": kw if kw is not None else "",
                        "status_updated_utc": evse.get("last_updated", ""),
                    })
                    site_rows.append({
                        "operator": name, "location_id": loc_id,
                        "location_name": loc_name, "address": loc.get("address", ""),
                        "postcode": loc.get("postal_code", ""),
                        "lat": pt[0], "lon": pt[1], "distance_miles": round(dist, 2),
                        "evse_uid": uid, "evse_id": evse.get("evse_id", ""),
                        "max_kw": kw if kw is not None else "",
                        "connectors": "; ".join(
                            f"{c.get('standard', '?')} {_connector_kw(c) or '?'}kW"
                            for c in evse.get("connectors") or []),
                        "last_seen_utc": stamp,
                    })
                    log["evses"] += 1
            log["ok"] = 1
        except Exception as exc:  # one bad feed must not stop the others
            log["error"] = f"{type(exc).__name__}: {exc}"[:300]
        log_rows.append(log)

    # ---- summary to stdout (shows in the Actions log) ----
    for lg in log_rows:
        flag = "OK " if lg["ok"] else "ERR"
        print(f"[{flag}] {lg['operator']}: {lg['locations_in_radius']} sites in radius "
              f"of {lg['locations_total']}, {lg['evses']} bays {lg['error']}")
    counts: dict[str, int] = {}
    for r in status_rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("Status counts:", counts or "none")

    if dry_run:
        return 0

    _append_csv(STATUS_DIR / f"{now:%Y-%m-%d}.csv", STATUS_FIELDS, status_rows)
    _append_csv(DATA / "poll_log.csv", LOG_FIELDS, log_rows)
    _merge_sites(site_rows)
    return 0


def _merge_sites(new_rows: list) -> None:
    """Keep a running register of every bay ever seen, refreshed each poll."""
    path = DATA / "sites.csv"
    existing = {}
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                existing[(row["operator"], row["evse_uid"])] = row
    for row in new_rows:
        existing[(row["operator"], str(row["evse_uid"]))] = row
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SITE_FIELDS)
        w.writeheader()
        for key in sorted(existing):
            w.writerow(existing[key])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--config", default=str(ROOT / "feeds.yaml"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    with open(args.config, encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    return poll(config, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
