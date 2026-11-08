"""Henter Garmin-data (døgnsammendrag, søvn, HRV, stress/Body Battery, puls og
skritt) for de siste dagene og lagrer rå JSON per dato i data/raw/garmin/days/.

Historikken bygges opp over tid: hver dato har én fil, datoer utenfor
henteperioden røres ikke, og hentes en dato på nytt, erstatter den nyeste
hentingen den gamle. En dato skrives bare hvis alle kall lyktes og Garmin
faktisk hadde data, slik at en mislykket henting aldri sletter gode data.

Kjør fra prosjektroten:
    .venv/bin/python -m src.fetch_garmin              # siste N dager (config.toml)
    .venv/bin/python -m src.fetch_garmin --days 10
    .venv/bin/python -m src.fetch_garmin --since 2026-11-03
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from src.config import GARMIN_DAYS_DIR, ROOT, load_config

TOKEN_DIR = "~/.garminconnect"  # innloggingstokens lagres utenfor repoet
PLACEHOLDER = "endre-meg"
NO_DATA = "ingen data hos Garmin"

# Nøkkel i JSON-filen -> metode i garminconnect (alle tar én dato)
ENDPOINTS = {
    "user_summary": "get_user_summary",
    "sleep": "get_sleep_data",
    "hrv": "get_hrv_data",
    "stress": "get_stress_data",  # inneholder også Body Battery gjennom døgnet
    "heart_rates": "get_heart_rates",
    "steps": "get_steps_data",
}


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


def has_data(payload: dict) -> bool:
    """Sant hvis Garmin har registrert noe for datoen (klokka var i bruk)."""
    stress = payload.get("stress") or {}
    hr = payload.get("heart_rates") or {}
    sleep = (payload.get("sleep") or {}).get("dailySleepDTO") or {}
    return bool(
        stress.get("stressValuesArray")
        or hr.get("heartRateValues")
        or sleep.get("sleepTimeSeconds")
    )


def fetch_day(api: Garmin, day: str) -> tuple[dict | None, str]:
    """Henter alle endepunkter for én dato. Returnerer (payload, status)."""
    payload = {
        "date": day,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    errors = []
    for key, method in ENDPOINTS.items():
        try:
            payload[key] = getattr(api, method)(day)
        except GarminConnectConnectionError as err:
            # Noen endepunkter kaster feil når dagen er tom – avgjøres under.
            payload[key] = None
            errors.append(f"{method}: {err}")
    if not has_data(payload):
        return None, NO_DATA
    if errors:
        return None, "feil fra " + "; ".join(errors)
    return payload, "ok"


def save_day(payload: dict) -> None:
    """Skriver datoens fil atomisk, slik at en avbrutt kjøring ikke etterlater halve filer."""
    GARMIN_DAYS_DIR.mkdir(parents=True, exist_ok=True)
    target = GARMIN_DAYS_DIR / f"{payload['date']}.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)


def fetch(days: int | None = None, since: date | None = None) -> int:
    """Henter og lagrer dagene. Returnerer antall datoer som feilet."""
    end = date.today()
    if since is None:
        days = days or load_config()["garmin"]["days"]
        since = end - timedelta(days=days - 1)

    try:
        api = login()
        failed = 0
        day = since
        while day <= end:
            payload, status = fetch_day(api, day.isoformat())
            if payload:
                save_day(payload)
                print(f"  {day}  lagret")
            else:
                print(f"  {day}  hoppet over ({status})")
                failed += status != NO_DATA
            day += timedelta(days=1)
    except GarminConnectAuthenticationError as err:
        sys.exit(f"Innlogging feilet: {err}")
    except GarminConnectTooManyRequestsError as err:
        sys.exit(f"Garmin begrenser antall forespørsler: {err}")
    except GarminConnectConnectionError as err:
        sys.exit(f"Kunne ikke hente data fra Garmin: {err}")
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--days", type=int, help="antall dager bakover, inkludert i dag")
    group.add_argument("--since", type=date.fromisoformat, help="hent fra og med denne datoen")
    args = parser.parse_args()

    print("Henter Garmin-data …")
    failed = fetch(days=args.days, since=args.since)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
