"""Morgenkommandoen: oppdaterer data og lager dagens melding fra AI-coachen.

Kjør fra prosjektroten:
    .venv/bin/python -m src.morning              # oppdater og vis dagens melding
    .venv/bin/python -m src.morning --show-data  # vis også dataene som sendes til modellen
    .venv/bin/python -m src.morning --dry-run    # vis dataene uten å kalle modellen
    .venv/bin/python -m src.morning --force      # lag ny melding selv om dagens finnes

Det lages én melding per dag. Kjøres kommandoen igjen samme dag, vises
meldingen som allerede er laget.
"""

import argparse
import json
import os
import sys
from datetime import date

from dotenv import load_dotenv

from src import build_db, coach, coach_data, fetch_garmin
from src.config import COACH_DIR, DB_PATH, ROOT, load_config


def update_data() -> None:
    print("Oppdaterer data …")
    try:
        fetch_garmin.fetch()
    except SystemExit as err:
        print(f"  Garmin-henting feilet, bruker lagrede data: {err}", file=sys.stderr)
    build_db.build()


def main() -> None:
    parser = argparse.ArgumentParser(description="Dagens melding fra AI-coachen.")
    parser.add_argument("--show-data", action="store_true", help="vis systemprompt og data som sendes")
    parser.add_argument("--dry-run", action="store_true", help="vis dataene, men ikke kall modellen")
    parser.add_argument("--force", action="store_true", help="lag ny melding selv om dagens finnes")
    parser.add_argument("--no-update", action="store_true", help="ikke hent nye Garmin-data først")
    parser.add_argument("--model", help="overstyr modellen i config.toml (f.eks. for å sammenligne)")
    args = parser.parse_args()

    cfg = load_config()["coach"]
    if args.model:
        cfg["model"] = args.model
    today = date.today()

    if not (args.force or args.dry_run) and (existing := coach.latest_for_day(COACH_DIR, today.isoformat())):
        print(f"\nDagens melding ({existing['created_at'][11:16]} UTC):\n")
        print(existing["message"])
        print("\n(Allerede laget i dag – bruk --force for å lage en ny.)")
        return

    if not args.no_update:
        update_data()

    payload = coach_data.collect(DB_PATH, today, cfg)

    if args.show_data or args.dry_run:
        print("\n===== SYSTEMPROMPT =====\n")
        print(coach.system_prompt(cfg["max_sentences"]))
        print("===== DATA SOM SENDES =====\n")
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    if args.dry_run:
        print("\n(--dry-run: modellen ble ikke kalt, ingenting lagret.)")
        return

    load_dotenv(ROOT / ".env")
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("Mangler OPENROUTER_API_KEY i .env.")

    try:
        result = coach.generate(api_key, cfg["model"], payload, cfg["max_sentences"])
    except coach.CoachError as err:
        sys.exit(f"Kunne ikke lage melding: {err}")

    path = coach.save(COACH_DIR, today.isoformat(), cfg["model"], payload, result)
    build_db.build()  # tar med den nye meldingen i coach_messages

    print(f"\n===== DAGENS MELDING ({today:%d.%m.%Y}) =====\n")
    print(result["message"])
    if result["warnings"]:
        print("\nAdvarsel – meldingen bryter fortsatt regler: " + "; ".join(result["warnings"]), file=sys.stderr)
    if args.show_data and result["first_draft_violations"]:
        print("\n----- Første utkast (skrevet om) -----\n")
        print(result["first_draft"])
        print("\nBrøt: " + "; ".join(result["first_draft_violations"]))
    if args.show_data:
        print(
            f"\n[{cfg['model']} · {result['attempts']} kall · {result['input_tokens']} inn / "
            f"{result['output_tokens']} ut tokens · ${result['cost_usd']} · lagret i {path.relative_to(ROOT)}]"
        )


if __name__ == "__main__":
    main()
