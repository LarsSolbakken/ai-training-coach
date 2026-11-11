"""Bygger data/training.db på nytt fra rådataene i data/raw/.

Databasen er avledet: den slettes og bygges fra bunnen hver gang, så
rådataene er alltid fasiten og regelendringer slår inn ved neste kjøring.

Kjør fra prosjektroten:  .venv/bin/python -m src.build_db
"""

import sqlite3

from src import coach, parse_garmin, parse_polar, parse_strength
from src.config import (
    COACH_DIR,
    DB_PATH,
    GARMIN_DAYS_DIR,
    POLAR_DIR,
    STRENGTH_CSV,
    load_config,
)

SCHEMA = """
CREATE TABLE exercises (
    name            TEXT PRIMARY KEY,
    is_bodyweight   INTEGER NOT NULL          -- 1 = loggført vekt er ekstravekt
);

CREATE TABLE strength_sessions (
    id              INTEGER PRIMARY KEY,
    start_time      TEXT NOT NULL UNIQUE,     -- lokal tid, økten starter
    workout_name    TEXT
);

CREATE TABLE strength_sets (
    id              INTEGER PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES strength_sessions(id),
    exercise        TEXT NOT NULL REFERENCES exercises(name),
    set_no          INTEGER NOT NULL,         -- renummerert fra 1
    set_no_raw      INTEGER NOT NULL,         -- som i appen
    load_kg         REAL NOT NULL,            -- loggført vekt (ekstravekt for kroppsvektøvelser)
    total_load_kg   REAL NOT NULL,            -- inkl. kroppsvekt for kroppsvektøvelser
    reps            INTEGER NOT NULL,
    tonnage_kg      REAL NOT NULL,            -- total_load_kg × reps
    e1rm_kg         REAL,                     -- formel fra config.toml, ytre last, reps + RIR ≤ 5
    rir             REAL,                     -- fra notat; RIR = 10 − RPE hvis bare RPE
    rpe             REAL,                     -- som skrevet i notatet
    rir_source      TEXT,                     -- 'RIR' | 'RPE' | NULL
    is_hard         INTEGER,                  -- 1 = RIR ≤ 3, 0 = RIR > 3, NULL = ukjent
    notes           TEXT
);
CREATE INDEX idx_sets_session ON strength_sets(session_id);
CREATE INDEX idx_sets_exercise ON strength_sets(exercise);

CREATE TABLE cardio_sessions (
    id              TEXT PRIMARY KEY,         -- Polar-ID
    sport           TEXT NOT NULL,            -- fra sport_id
    sport_id        INTEGER NOT NULL,
    name_raw        TEXT,                     -- navn fra Polar (kan være feil)
    start_time      TEXT NOT NULL,            -- lokal tid
    stop_time       TEXT,
    tz_offset_min   INTEGER,
    duration_s      INTEGER,
    distance_m      REAL,                     -- NULL for judo
    calories        INTEGER,
    hr_avg          INTEGER,
    hr_min          INTEGER,
    hr_max          INTEGER,
    speed_avg_kmh   REAL,
    speed_max_kmh   REAL,
    cardio_load     REAL,
    training_benefit TEXT,
    recovery_h      REAL,
    running_index   INTEGER
);

CREATE TABLE cardio_hr_zones (
    session_id      TEXT NOT NULL REFERENCES cardio_sessions(id),
    zone            INTEGER NOT NULL,
    lower_bpm       INTEGER,
    upper_bpm       INTEGER,
    seconds         INTEGER NOT NULL,
    PRIMARY KEY (session_id, zone)
);

CREATE TABLE cardio_samples (
    session_id      TEXT NOT NULL REFERENCES cardio_sessions(id),
    t_s             REAL NOT NULL,            -- sekunder fra start
    hr_bpm          INTEGER,
    speed_kmh       REAL,
    distance_m      REAL,
    PRIMARY KEY (session_id, t_s)
) WITHOUT ROWID;

CREATE TABLE cardio_route (
    session_id      TEXT NOT NULL REFERENCES cardio_sessions(id),
    t_s             REAL NOT NULL,
    lat             REAL,
    lon             REAL
);
CREATE INDEX idx_route_session ON cardio_route(session_id);

CREATE TABLE daily_wellness (
    date                    TEXT PRIMARY KEY,
    bedtime                 TEXT,             -- lokal tid, natten som endte denne datoen
    wake_time               TEXT,
    sleep_s                 INTEGER,
    deep_sleep_s            INTEGER,
    light_sleep_s           INTEGER,
    rem_sleep_s             INTEGER,
    awake_s                 INTEGER,
    awake_count             INTEGER,
    sleep_score             INTEGER,
    sleep_quality           TEXT,
    sleep_avg_hr            REAL,
    sleep_avg_stress        REAL,
    sleep_respiration       REAL,
    skin_temp_dev_c         REAL,
    hrv_night_avg           INTEGER,          -- ms
    hrv_night_5min_high     INTEGER,
    hrv_weekly_avg          INTEGER,
    hrv_baseline_low        INTEGER,
    hrv_baseline_high       INTEGER,
    hrv_status              TEXT,
    resting_hr              INTEGER,
    resting_hr_7d_avg       INTEGER,
    min_hr                  INTEGER,
    max_hr                  INTEGER,
    steps                   INTEGER,
    step_goal               INTEGER,
    distance_m              REAL,
    active_kcal             REAL,
    total_kcal              REAL,
    intensity_min_moderate  INTEGER,
    intensity_min_vigorous  INTEGER,
    stress_avg              INTEGER,
    stress_max              INTEGER,
    stress_rest_s           INTEGER,
    stress_low_s            INTEGER,
    stress_medium_s         INTEGER,
    stress_high_s           INTEGER,
    bb_high                 INTEGER,
    bb_low                  INTEGER,
    bb_charged              INTEGER,
    bb_drained              INTEGER,
    bb_at_wake              INTEGER,
    bb_sleep_change         INTEGER,
    respiration_waking_avg  REAL,
    fetched_at              TEXT              -- når dataene ble hentet (UTC)
);

CREATE TABLE garmin_intraday (
    ts_utc          TEXT PRIMARY KEY,
    ts_local        TEXT NOT NULL,
    date            TEXT NOT NULL REFERENCES daily_wellness(date),
    stress_level    INTEGER,                  -- 0–100, NULL når ikke målbart
    stress_status   TEXT,                     -- measured | too_much_motion | insufficient_data
    body_battery    INTEGER,
    bb_status       TEXT,                     -- MEASURED | MODELED | RESET
    heart_rate      INTEGER,
    steps           INTEGER,                  -- skritt i 15-min-intervallet som starter her
    activity_level  TEXT
) WITHOUT ROWID;
CREATE INDEX idx_intraday_date ON garmin_intraday(date);

CREATE TABLE coach_messages (
    id              INTEGER PRIMARY KEY,
    date            TEXT NOT NULL,            -- dagen meldingen gjelder
    created_at      TEXT NOT NULL,            -- UTC
    model           TEXT NOT NULL,
    prompt_version  TEXT,                     -- hash av systemprompten
    message         TEXT NOT NULL,
    warnings        TEXT,                     -- regelbrudd som gjensto etter omskriving
    first_draft     TEXT,                     -- modellens første utkast
    first_draft_violations TEXT,              -- reglene første utkast brøt (NULL = ingen)
    attempts        INTEGER,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cost_usd        REAL,
    input_json      TEXT NOT NULL             -- dataene modellen fikk
);
CREATE INDEX idx_coach_date ON coach_messages(date);

-- Uken starter mandag: date(x, 'weekday 0', '-6 days') = mandagen i x sin uke.
CREATE VIEW v_strength_session AS
SELECT s.id AS session_id,
       s.start_time,
       s.workout_name,
       COUNT(*)                    AS sets,
       ROUND(SUM(t.tonnage_kg), 1) AS tonnage_kg,
       COUNT(t.rir)                AS sets_with_rir,
       SUM(t.is_hard)              AS hard_sets
FROM strength_sessions s
JOIN strength_sets t ON t.session_id = s.id
GROUP BY s.id;

CREATE VIEW v_weekly_exercise_sets AS
SELECT date(s.start_time, 'weekday 0', '-6 days') AS week_start,
       t.exercise,
       COUNT(*)                    AS sets,
       COUNT(t.rir)                AS sets_with_rir,
       SUM(t.is_hard)              AS hard_sets,
       ROUND(SUM(t.tonnage_kg), 1) AS tonnage_kg
FROM strength_sets t
JOIN strength_sessions s ON s.id = t.session_id
GROUP BY week_start, t.exercise;

CREATE VIEW v_best_e1rm AS
SELECT date(s.start_time, 'weekday 0', '-6 days') AS week_start,
       t.exercise,
       MAX(t.e1rm_kg) AS best_e1rm_kg
FROM strength_sets t
JOIN strength_sessions s ON s.id = t.session_id
WHERE t.e1rm_kg IS NOT NULL
GROUP BY week_start, t.exercise;
"""


def insert(con: sqlite3.Connection, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols = list(rows[0])
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    con.executemany(sql, [tuple(r[c] for c in cols) for r in rows])


def build() -> dict[str, int]:
    cfg = load_config()
    bw_exercises = set(cfg["strength"]["bodyweight_exercises"])

    strength_sessions, strength_sets = parse_strength.parse(
        STRENGTH_CSV,
        cfg["strength"]["bodyweight_kg"],
        bw_exercises,
        cfg["strength"]["e1rm_formula"],
    )
    exercises = [
        {"name": name, "is_bodyweight": int(name in bw_exercises)}
        for name in sorted({s["exercise"] for s in strength_sets})
    ]
    cardio, zones, samples, route = parse_polar.parse(POLAR_DIR)
    daily, intraday = parse_garmin.parse(GARMIN_DAYS_DIR, cfg["garmin"]["timezone"])

    tables = {
        "exercises": exercises,
        "strength_sessions": strength_sessions,
        "strength_sets": strength_sets,
        "cardio_sessions": cardio,
        "cardio_hr_zones": zones,
        "cardio_samples": samples,
        "cardio_route": route,
        "daily_wellness": daily,
        "garmin_intraday": intraday,
        "coach_messages": coach.load_all(COACH_DIR),
    }

    # Bygg i en midlertidig fil og bytt inn til slutt, så en feil aldri
    # etterlater en halvferdig database.
    tmp = DB_PATH.with_suffix(".db.tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript(SCHEMA)
        for table, rows in tables.items():
            insert(con, table, rows)
        con.commit()
    finally:
        con.close()
    tmp.replace(DB_PATH)
    return {table: len(rows) for table, rows in tables.items()}


def main() -> None:
    counts = build()
    print(f"Bygget {DB_PATH.name}:")
    for table, n in counts.items():
        print(f"  {table:<20} {n:>8} rader")


if __name__ == "__main__":
    main()
