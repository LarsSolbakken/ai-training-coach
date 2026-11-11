"""Starter dashbordet lokalt.

Kjør fra prosjektroten:  .venv/bin/python -m src.dashboard [--port 8501] [--no-browser]

Serveren lytter bare på 127.0.0.1, slik at helsedataene ikke er tilgjengelige
fra andre maskiner på nettverket, og Streamlits bruksstatistikk er slått av.
"""

import argparse
import subprocess
import sys

from src.config import DB_PATH, ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="Start treningsdashbordet lokalt.")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--no-browser", action="store_true", help="ikke åpne nettleseren automatisk")
    args = parser.parse_args()

    if not DB_PATH.exists():
        sys.exit("Fant ikke data/training.db – kjør .venv/bin/python -m src.update først.")

    cmd = [
        sys.executable, "-m", "streamlit", "run", str(ROOT / "src" / "dashboard_app.py"),
        "--server.address=127.0.0.1",
        f"--server.port={args.port}",
        f"--server.headless={'true' if args.no_browser else 'false'}",
        "--browser.gatherUsageStats=false",
    ]
    print(f"Dashbordet kjører på http://127.0.0.1:{args.port}  (Ctrl+C for å stoppe)")
    try:
        subprocess.run(cmd, cwd=ROOT, check=False)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
