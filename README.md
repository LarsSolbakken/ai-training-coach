# ai-training-coach

## Oppsett

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fyll inn egne verdier
```

## Morgenmelding fra AI-coachen

```bash
.venv/bin/python -m src.morning              # oppdaterer data og viser dagens melding
.venv/bin/python -m src.morning --show-data  # viser også systemprompt og data som sendes
.venv/bin/python -m src.morning --dry-run    # viser dataene uten å kalle modellen
.venv/bin/python -m src.morning --force      # lager ny melding selv om dagens finnes
```

Krever `OPENROUTER_API_KEY` i `.env`. Modell og terskler står under `[coach]` i `config.toml`.
Én melding per dag: kjøres kommandoen igjen samme dag, vises meldingen som allerede er laget.
Meldingene lagres i `data/raw/coach/` (én JSON med data, prompt og svar per melding) og
lastes inn i tabellen `coach_messages`.

Modellen får siste 7 dager (søvn, HRV, hvilepuls, stress, Body Battery), økter og gårsdagens
stresstopper. Normalområdet er snitt ± 1 standardavvik over siste 60 dager og regnes som
etablert etter 14 dager med data. Vurderingene (innenfor/utenfor normalen, «tydelig utenfor»
når HRV er lav og hvilepulsen høy flere dager samtidig) regnes ut i koden, ikke av modellen.
Belastning vurderes som to separate signaler (ikke slått sammen), siste 7 dager mot ukesnittet
siste 4 uker: kondisjon (Polars cardio load) og styrke (tonnasje, og andel sett med RIR ≤ 3 når
RIR finnes). Forhold over 1,3 = høyere, under 0,8 = lavere. Er treningsloggen eldre enn 3 dager,
blir signalet «ukjent» og nevnes ikke. Grensene står i `config.toml`.
Når Garmin har HRV-status og baseline, slås den sammen med vår egen HRV-vurdering, og den mest
forsiktige vinner (LOW/POOR teller som tydelig utenfor). Modellen nevner ikke uenigheten.
Svaret sjekkes maskinelt (maks 5 setninger, ingen tall eller fagord); bryter det reglene,
bes modellen én gang om å skrive det om.

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

`config.toml`: kroppsvekt (brukes i tonnasje for kroppsvektøvelser), formel for estimert 1RM, hvilke øvelser som er kroppsvektøvelser, tidssone og antall Garmin-dager per oppdatering.

## Data

| Kilde | Rådata | Tabeller |
|---|---|---|
| Polar (eksport) | `data/raw/polar/*/training-session_*.json` | `cardio_sessions`, `cardio_hr_zones`, `cardio_samples`, `cardio_route` |
| Gym Day (CSV) | `data/raw/styrke/gym-day.csv` | `exercises`, `strength_sessions`, `strength_sets` |
| Garmin Connect | `data/raw/garmin/days/*.json` | `daily_wellness`, `garmin_intraday` |
| AI-coach | `data/raw/coach/*.json` | `coach_messages` |

Views: `v_strength_session` (sett, tonnasje og harde sett per økt), `v_weekly_exercise_sets` (sett per øvelse per uke, uken starter mandag), `v_best_e1rm` (beste estimerte 1RM per øvelse per uke).

Regler:

- Polar: `sport.id` er fasit for sport (1 = løping, 115 = judo); GPS ignoreres for judo.
- Styrke: tom/0 vekt på kroppsvektøvelser = kroppsvekt, > 0 = ekstravekt. Tonnasje = (kroppsvekt + ekstravekt) × reps for kroppsvektøvelser, ellers vekt × reps. Estimert 1RM bare fra sett der reps + RIR ≤ 5 (uten RIR: reps ≤ 5), med formelen valgt i `config.toml` (standard Brzycki: vekt × 36 / (37 − reps)); når RIR finnes, brukes reps + RIR i formelen.
- RIR/RPE leses fra notatfeltet («RIR 2», «RPE 8», «9.5 RPE», «RIR 10+» = 10). Bare RPE → RIR = 10 − RPE. Hardt sett = RIR ≤ 3.

## Struktur

- `src/` – kildekode
- `data/raw/` – rådata (ikke versjonert)
- `data/training.db` – SQLite-database bygget fra rådata (ikke versjonert)
