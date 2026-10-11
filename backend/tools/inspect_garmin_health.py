"""
Garmin Health Discovery Utility (developer tool, run manually by the user)

Captures raw Garmin Connect health responses (sleep, HRV, stress, Body
Battery, respiration, SpO2) for a single date to sanitized local JSON files,
so real response shapes can be inspected before any Django model is designed.

This script is NOT wired into the Django app, management commands, or the
dashboard sync. It does not touch DailyLog/Activity/ActivityLap or any
existing CSV/import files.

Authentication mirrors get_garmindata.py exactly (token reuse via
.garmintokens/, falling back to GARMIN_EMAIL/GARMIN_PASSWD credential login).
Nothing here modifies Garmin account data - every call is a read-only GET.

Usage (run from the repository root, with the project virtualenv):
    venv\\Scripts\\python.exe backend/tools/inspect_garmin_health.py --date 2026-08-31
    venv\\Scripts\\python.exe backend/tools/inspect_garmin_health.py --date 2026-08-31 --metric sleep

Output:
    backend/data/garmin_health_debug/<date>_<metric>.json
    (backend/data/ is already gitignored; nothing here is committed)
"""

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from garminconnect import Garmin

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOKEN_DIR = PROJECT_ROOT / ".garmintokens"
OUTPUT_DIR = PROJECT_ROOT / "backend" / "data" / "garmin_health_debug"

METRIC_CHOICES = ["sleep", "hrv", "stress", "body-battery", "respiration", "spo2", "all"]

# Key names that identify the account/device rather than health measurements.
# Matched case-insensitively as a substring of the dict key.
_SENSITIVE_KEY_PATTERNS = [
    "email", "token", "cookie", "auth", "session",
    "userid", "user_id", "profileid", "profile_id", "profilepk",
    "accountid", "account_id", "displayname", "display_name",
    "serialnumber", "serial_number", "deviceid", "device_id",
    "fullname", "full_name", "username",
]


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(pattern in normalized for pattern in _SENSITIVE_KEY_PATTERNS)


def sanitize(value):
    """Recursively redact obviously account-identifying fields, keep the rest as-is."""
    if isinstance(value, dict):
        result = {}
        for key, val in value.items():
            if _is_sensitive_key(str(key)):
                result[key] = "***REDACTED***"
            else:
                result[key] = sanitize(val)
        return result
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def parse_date(value: str) -> str:
    """Validate a YYYY-MM-DD date string (the format every inspected endpoint requires)."""
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date '{value}'. Use YYYY-MM-DD."
        ) from exc
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture raw Garmin health responses for one date into sanitized JSON files."
    )
    parser.add_argument("--date", required=True, type=parse_date, help="Date to inspect (YYYY-MM-DD).")
    parser.add_argument(
        "--metric",
        default="all",
        choices=METRIC_CHOICES,
        help="Which health category to fetch (default: all).",
    )
    return parser


def login() -> Garmin:
    """Log in using the same token-reuse pattern as get_garmindata.py."""
    email = os.getenv("GARMIN_EMAIL")
    passwd = os.getenv("GARMIN_PASSWD")
    if not email or not passwd:
        raise RuntimeError("GARMIN_EMAIL or GARMIN_PASSWD environment variable is missing")

    os.makedirs(TOKEN_DIR, exist_ok=True)
    client = Garmin(email, passwd)

    try:
        print("Trying to login using stored tokens...")
        client.login(str(TOKEN_DIR))
        print("Login with tokens successful!")
    except Exception as e:
        print(f"Stored tokens failed or not found: {e}")
        print("Performing full login with credentials...")
        client.login()
        client.garth.dump(str(TOKEN_DIR))
        print("Tokens saved successfully for future use.")

    return client


def fetch_metric(client: Garmin, metric: str, cdate: str):
    if metric == "sleep":
        return client.get_sleep_data(cdate)
    if metric == "hrv":
        return client.get_hrv_data(cdate)
    if metric == "stress":
        return client.get_stress_data(cdate)
    if metric == "body-battery":
        return client.get_body_battery(cdate, cdate)
    if metric == "respiration":
        return client.get_respiration_data(cdate)
    if metric == "spo2":
        return client.get_spo2_data(cdate)
    raise ValueError(f"Unknown metric: {metric}")


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    args = build_parser().parse_args()

    metrics = [m for m in METRIC_CHOICES if m != "all"] if args.metric == "all" else [args.metric]

    client = login()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for metric in metrics:
        print(f"Fetching {metric} for {args.date}...")
        try:
            raw = fetch_metric(client, metric, args.date)
        except Exception as e:
            print(f"  [WARN] {metric} failed: {e}")
            continue

        sanitized = sanitize(raw)
        out_path = OUTPUT_DIR / f"{args.date}_{metric.replace('-', '_')}.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(sanitized, f, indent=2, ensure_ascii=False, default=str)

        top_level = (
            list(sanitized.keys()) if isinstance(sanitized, dict)
            else f"list[{len(sanitized)}]" if isinstance(sanitized, list)
            else type(sanitized).__name__
        )
        print(f"  [OK] wrote {out_path} (top-level: {top_level})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
