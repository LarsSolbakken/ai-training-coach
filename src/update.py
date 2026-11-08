"""Oppdaterer alt: henter nye Garmin-data og bygger databasen på nytt.

Kjør fra prosjektroten:  .venv/bin/python -m src.update [--days N]

Feiler Garmin-hentingen, bygges databasen likevel av dataene som allerede
ligger lagret, og kommandoen avslutter med feilkode 1.
"""

import argparse
import sys

from src import build_db, fetch_garmin


def main() -> None:
    parser = argparse.ArgumentParser(description="Hent Garmin-data og bygg databasen på nytt.")
    parser.add_argument("--days", type=int, help="antall dager bakover (standard fra config.toml)")
    args = parser.parse_args()

    print("Henter Garmin-data …")
    try:
        ok = fetch_garmin.fetch(days=args.days) == 0
    except SystemExit as err:  # innlogging/nettverk – fortsett med lagrede data
        print(f"  Garmin-henting feilet: {err}", file=sys.stderr)
        ok = False

    print("Bygger databasen …")
    build_db.main()

    if not ok:
        print("Merk: noen Garmin-dager ble ikke oppdatert (se over).", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
