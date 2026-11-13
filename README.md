# ai-training-coach

En personlig AI-treningscoach som samler data fra pulsklokke, treningslogg og
Garmin-armbånd i én database, og hver morgen skriver en kort, rolig melding om
restitusjon og belastning. Et lokalt dashbord viser trendene over tid.

**Hva prosjektet viser:**

- **Dataintegrasjon:** tre ulike kilder (Polar JSON-eksport, Gym Day CSV og
  Garmin Connect API) renses og normaliseres til én SQLite-database med tabeller og views.
- **LLM i produksjonslignende flyt:** all analyse (normalområder, trender, belastning)
  regnes ut deterministisk i Python. Språkmodellen får bare ferdige vurderinger og skal
  formulere dem. Svaret sjekkes maskinelt (lengde, tall, fagord) og skrives om én gang
  ved regelbrudd. Prompt, data, første utkast og regelbrudd lagres for sporbarhet.
- **Personvern:** helsedata blir på egen maskin. Rådata og database er ikke versjonert,
  dashbordet kjører bare på `127.0.0.1` med bruksstatistikk av, og hemmeligheter ligger i `.env`.
- **Datavisualisering:** rolig dashbord uten daglige poengsummer eller rødt/grønt,
  med fargeblindsjekket palett og tabellvisning for hver graf.

## Verktøy

| Område | Verktøy |
|---|---|
| Språk | Python 3.11+ |
| Database | SQLite (bygges på nytt fra rådata, med views for ukeaggregater) |
| AI | OpenRouter API med Claude (Sonnet) – modell kan byttes i `config.toml` |
| Datakilder | `garminconnect` (Garmin Connect), Polar-eksport (JSON), Gym Day (CSV) |
| Dashbord | Streamlit, Altair, pandas |
| Annet | `requests`, `python-dotenv`, `tomllib` for konfigurasjon |

## Slik fungerer det

```
Polar (JSON) ─┐
Gym Day (CSV) ─┼─> parse_*.py ─> build_db.py ─> data/training.db ─┬─> coach_data.py ─> coach.py ─> morgenmelding
Garmin API ───┘   (rensing)                       (SQLite)         └─> dashboard_app.py (Streamlit)
```

1. `fetch_garmin.py` henter søvn, HRV, hvilepuls, stress og Body Battery fra Garmin Connect.
2. `parse_polar.py`, `parse_strength.py` og `parse_garmin.py` leser og renser rådataene.
3. `build_db.py` bygger SQLite-databasen på nytt fra alle rådata.
4. `coach_data.py` regner ut personlig normalområde (snitt ± 1 SD siste 60 dager),
   trender, stresstopper koblet til økter og belastning siste 7 dager mot 4-ukerssnitt.
5. `coach.py` sender de ferdige vurderingene til språkmodellen, kontrollerer svaret og lagrer det.
6. `dashboard_app.py` viser estimert 1RM, økter per uke og søvn/hvilepuls/HRV mot normalområdet.

## Kjøre prosjektet

Krever Python 3.11+ og egne data: en Garmin-konto, en OpenRouter-nøkkel og eventuelt
eksport fra Polar og Gym Day. Ingen persondata følger med i repoet.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fyll inn GARMIN_EMAIL, GARMIN_PASSWORD og OPENROUTER_API_KEY
```

Legg eventuelle eksporter i `data/raw/` (se [Data](#data)), og kjør:

```bash
python -m src.update      # henter siste dager fra Garmin og bygger databasen
python -m src.morning     # oppdaterer data og viser dagens melding fra coachen
python -m src.dashboard   # åpner dashbordet på http://127.0.0.1:8501
```

Første gang ber Garmin kanskje om en MFA-kode. Kjør da kommandoen i en vanlig terminal.
Innloggingstokens lagres i `~/.garminconnect`, utenfor repoet.

Nyttige flagg:

```bash
python -m src.morning --dry-run     # viser dataene uten å kalle modellen
python -m src.morning --show-data   # viser også systemprompt og data som sendes
python -m src.morning --force       # lager ny melding selv om dagens finnes
python -m src.update --days 10      # henter flere dager bakover
python -m src.fetch_garmin --since 2026-11-01
python -m src.build_db              # bygger bare databasen på nytt
```

## Detaljer

### AI-coachen

- Én melding per dag, maks fire setninger, uten rå tall eller fagord.
- Vurderingene (innenfor/utenfor normalen, «tydelig utenfor» når HRV er lav og hvilepulsen
  høy flere dager samtidig) regnes ut i koden, ikke av modellen. Normalen regnes som etablert
  etter 14 dager med data.
- Belastning vurderes som to separate signaler, siste 7 dager mot ukesnittet siste 4 uker:
  kondisjon (Polars cardio load) og styrke (tonnasje og andel sett med RIR ≤ 3).
  Er treningsloggen eldre enn 3 dager, blir signalet «ukjent» og nevnes ikke.
- Når Garmin har HRV-status og baseline, slås den sammen med den egne vurderingen,
  og den mest forsiktige vinner.
- Meldinger lagres i `data/raw/coach/` (data, prompt, første utkast og svar) og i tabellen
  `coach_messages`.

### Innstillinger

`config.toml` styrer modell, terskler for normalområde og belastning, kroppsvekt
(til tonnasje for kroppsvektøvelser), formel for estimert 1RM (Brzycki eller Epley),
tidssone og antall Garmin-dager per oppdatering.

### Data

| Kilde | Rådata | Tabeller |
|---|---|---|
| Polar (eksport) | `data/raw/polar/*/training-session_*.json` | `cardio_sessions`, `cardio_hr_zones`, `cardio_samples`, `cardio_route` |
| Gym Day (CSV) | `data/raw/styrke/gym-day.csv` | `exercises`, `strength_sessions`, `strength_sets` |
| Garmin Connect | `data/raw/garmin/days/*.json` | `daily_wellness`, `garmin_intraday` |
| AI-coach | `data/raw/coach/*.json` | `coach_messages` |

Views: `v_strength_session` (sett, tonnasje og harde sett per økt),
`v_weekly_exercise_sets` (sett per øvelse per uke) og `v_best_e1rm` (beste estimerte 1RM per uke).

Regler for rensing:

- **Polar:** `sport.id` avgjør sporten (1 = løping, 115 = judo), og GPS ignoreres for judo.
- **Styrke:** reparerer kommaer i notatfeltet som ikke er satt i anførselstegn, og nummererer
  sett på nytt. Tom eller 0 vekt på kroppsvektøvelser betyr kroppsvekt. Estimert 1RM bare fra
  sett med reps + RIR ≤ 5. RIR/RPE leses fra notatfeltet («RIR 2», «RPE 8»), og bare RPE gir
  RIR = 10 − RPE.

## Struktur

```
src/            kildekode (henting, parsing, database, coach, dashbord)
config.toml     innstillinger
data/raw/       rådata (ikke versjonert)
data/training.db  SQLite-database bygget fra rådata (ikke versjonert)
```
