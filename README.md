# ai-training-coach

## Oppsett

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fyll inn egne verdier
```

## Daglig oppdatering

```bash
.venv/bin/python -m src.update            # henter siste dager fra Garmin og bygger databasen
.venv/bin/python -m src.update --days 10  # henter flere dager bakover
```

Første gang ber Garmin kanskje om en MFA-kode – kjør da kommandoen i en vanlig terminal.

Delkommandoer:

- `python -m src.fetch_garmin [--days N | --since ÅÅÅÅ-MM-DD]` – henter Garmin-data til `data/raw/garmin/days/` (én JSON per dato; nyeste henting vinner, eldre datoer røres ikke)
- `python -m src.build_db` – bygger `data/training.db` på nytt fra alle rådata

## Innstillinger

`config.toml`: kroppsvekt (brukes i tonnasje for kroppsvektøvelser), hvilke øvelser som er kroppsvektøvelser, tidssone og antall Garmin-dager per oppdatering.

## Data

| Kilde | Rådata | Tabeller |
|---|---|---|
| Polar (eksport) | `data/raw/polar/*/training-session_*.json` | `cardio_sessions`, `cardio_hr_zones`, `cardio_samples`, `cardio_route` |
| Gym Day (CSV) | `data/raw/styrke/gym-day.csv` | `exercises`, `strength_sessions`, `strength_sets` |
| Garmin Connect | `data/raw/garmin/days/*.json` | `daily_wellness`, `garmin_intraday` |

Views: `v_strength_session` (sett, tonnasje og harde sett per økt), `v_weekly_exercise_sets` (sett per øvelse per uke, uken starter mandag), `v_best_e1rm` (beste estimerte 1RM per øvelse per uke).

Regler:

- Polar: `sport.id` er fasit for sport (1 = løping, 115 = judo); GPS ignoreres for judo.
- Styrke: tom/0 vekt på kroppsvektøvelser = kroppsvekt, > 0 = ekstravekt. Tonnasje = (kroppsvekt + ekstravekt) × reps for kroppsvektøvelser, ellers vekt × reps. Estimert 1RM (Epley) bare fra sett med 1–5 reps.
- RIR/RPE leses fra notatfeltet («RIR 2», «RPE 8», «9.5 RPE», «RIR 10+» = 10). Bare RPE → RIR = 10 − RPE. Hardt sett = RIR ≤ 3.

## Struktur

- `src/` – kildekode
- `data/raw/` – rådata (ikke versjonert)
- `data/training.db` – SQLite-database bygget fra rådata (ikke versjonert)
