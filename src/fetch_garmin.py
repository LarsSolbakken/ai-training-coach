"""Henter daglig stress, søvn og Body Battery fra Garmin Connect for de siste
30 dagene og lagrer resultatet som CSV i data/raw/.

Kjør fra prosjektroten:  .venv/bin/python src/fetch_garmin.py
"""

import csv
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
TOKEN_DIR = "~/.garminconnect"  # innloggingstokens lagres utenfor repoet
DAYS = 30
MAX_RANGE = 28  # Garmin avviser intervaller på mer enn 28 dager
PLACEHOLDER = "endre-meg"


def prompt_mfa() -> str:
    if not sys.stdin.isatty():
        sys.exit(
            "Garmin ber om MFA-kode. Kjør skriptet én gang i en vanlig terminal "
            "for å taste inn koden – deretter gjenbrukes lagrede tokens."
        )
    return input("MFA-kode fra Garmin: ").strip()


def login() -> Garmin:
    load_dotenv(ROOT / ".env")
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password or PLACEHOLDER in (email, password):
        sys.exit("Fyll inn GARMIN_EMAIL og GARMIN_PASSWORD i .env først.")

    api = Garmin(email, password, prompt_mfa=prompt_mfa)
    api.login(TOKEN_DIR)
    return api


def fetch_range(method, start: date, end: date) -> list[dict]:
    """Kaller en Garmin-metode med (start, slutt) i biter på maks MAX_RANGE dager."""
    rows = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=MAX_RANGE - 1), end)
        rows += method(chunk_start.isoformat(), chunk_end.isoformat()) or []
        chunk_start = chunk_end + timedelta(days=1)
    return rows


def by_date(rows: list[dict], prefix: str) -> dict[str, dict]:
    """Gjør Garmins dagsrader om til {dato: {kolonne: verdi}}.

    Feltene ligger enten direkte på raden eller under "values"; nestede
    lister/objekter (f.eks. Body Battery-kurven gjennom døgnet) hoppes over.
    """
    result = {}
    for row in rows or []:
        day = row.get("calendarDate") or row.get("date")
        if not day:
            continue
        values = row.get("values", row)
        result[day] = {
            f"{prefix}_{key}": value
            for key, value in values.items()
            if key not in ("calendarDate", "date") and not isinstance(value, (dict, list))
        }
    return result


def main() -> None:
    end = date.today()
    start = end - timedelta(days=DAYS - 1)
    s, e = start.isoformat(), end.isoformat()

    try:
        api = login()
        sources = [
            by_date(fetch_range(api.get_daily_stress_stats, start, end), "stress"),
            by_date(fetch_range(api.get_sleep_daily, start, end), "sleep"),
            by_date(fetch_range(api.get_daily_body_battery_stats, start, end), "bb"),
            by_date(fetch_range(api.get_body_battery, start, end), "bb"),
        ]
    except GarminConnectAuthenticationError as err:
        sys.exit(f"Innlogging feilet: {err}")
    except GarminConnectTooManyRequestsError as err:
        sys.exit(f"Garmin begrenser antall forespørsler: {err}")
    except GarminConnectConnectionError as err:
        sys.exit(f"Kunne ikke hente data fra Garmin: {err}")

    rows = []
    for i in range(DAYS):
        day = (start + timedelta(days=i)).isoformat()
        row = {"date": day}
        for source in sources:
            row.update(source.get(day, {}))
        rows.append(row)

    columns = ["date"] + sorted({col for row in rows for col in row} - {"date"})
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = RAW_DIR / f"garmin_daily_{s}_{e}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Lagret {len(rows)} dager og {len(columns)} kolonner til {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
