"""Henter dataene AI-coachen får: siste 7 dager, eget normalområde,
trender, økter og gårsdagens stresstopper med mulig forklaring.

Normalområdet regnes per måling som snitt ± 1 standardavvik over de siste
`normal_window_days` dagene. Det regnes som etablert først når det finnes
minst `min_days_for_normal` dager med data. Vurderingene (bedre/innenfor/
dårligere, «tydelig utenfor») regnes ut her, så modellen bare formulerer dem.
"""

import sqlite3
import statistics
from datetime import date, datetime, timedelta

WEEKDAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]

# nøkkel: (beskrivelse, kolonne, bedre retning, enhet, natt/dag, minste spredning)
# «natt»-målinger for dato D gjelder natten som endte morgenen D, og dagens
# verdi er klar om morgenen. «dag»-målinger er først komplette dagen etter.
METRICS = {
    "hrv": ("Nattlig HRV", "hrv_night_avg", "høyere", "ms", "natt", 2.0),
    "hvilepuls": ("Hvilepuls", "resting_hr", "lavere", "slag/min", "natt", 1.0),
    "søvnlengde": ("Søvnlengde", "sleep_s", "høyere", "timer", "natt", 0.25),
    "leggetid": ("Leggetid", "bedtime", "tidligere", "min fra midnatt", "natt", 15.0),
    "body_battery_morgen": ("Body Battery ved oppvåkning", "bb_at_wake", "høyere", "0–100", "natt", 3.0),
    "stress": ("Gjennomsnittlig stress gjennom dagen", "stress_avg", "lavere", "0–100", "dag", 2.0),
}


def _minutes_from_midnight(ts: str) -> float:
    """Leggetid som minutter fra midnatt; før midnatt blir negativt (23:30 = −30)."""
    t = datetime.fromisoformat(ts)
    m = t.hour * 60 + t.minute
    return m - 1440 if t.hour >= 12 else m


def _clock(minutes: float) -> str:
    m = round(minutes) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _value(row: sqlite3.Row, metric: str) -> float | None:
    col = METRICS[metric][1]
    v = row[col]
    if v is None:
        return None
    if metric == "søvnlengde":
        return v / 3600
    if metric == "leggetid":
        return _minutes_from_midnight(v)
    return float(v)


def _fmt(metric: str, v: float | None):
    if v is None:
        return None
    if metric == "leggetid":
        return _clock(v)
    return round(v, 2 if metric == "søvnlengde" else 1)


def _worse(metric: str, v: float, lo: float, hi: float) -> bool:
    return v < lo if METRICS[metric][2] in ("høyere",) else v > hi


def _better(metric: str, v: float, lo: float, hi: float) -> bool:
    return v > hi if METRICS[metric][2] in ("høyere",) else v < lo


def assess_metric(metric: str, series: dict[date, float], end: date, cfg: dict) -> dict:
    """Normalområde, siste-3/7-dagers snitt og vurdering for én måling."""
    window = [end - timedelta(days=i) for i in range(cfg["normal_window_days"])]
    values = [series[d] for d in window if d in series]
    last7 = [series[d] for d in (end - timedelta(days=i) for i in range(7)) if d in series]
    last3_days = [end - timedelta(days=i) for i in range(3)]
    last3 = [series[d] for d in last3_days if d in series]
    prev4 = [series[d] for d in (end - timedelta(days=i) for i in range(3, 7)) if d in series]

    out = {
        "beskrivelse": METRICS[metric][0],
        "enhet": METRICS[metric][3],
        "bedre_retning": METRICS[metric][2],
        "dager_med_data": len(values),
    }
    if len(values) < 3 or len(last3) < 2:
        out["vurdering"] = "for lite data"
        return out

    mean = statistics.mean(values)
    sd = max(statistics.stdev(values), METRICS[metric][5])
    lo, hi = mean - sd, mean + sd
    if METRICS[metric][3] == "0–100":
        lo, hi = max(lo, 0.0), min(hi, 100.0)
    recent = statistics.mean(last3)
    worse_last3 = sum(_worse(metric, series[d], lo, hi) for d in last3_days if d in series)
    worse_last7 = sum(_worse(metric, v, lo, hi) for v in last7)

    if _worse(metric, recent, lo, hi):
        status = "dårligere enn normalt"
    elif _better(metric, recent, lo, hi):
        status = "bedre enn normalt"
    else:
        status = "innenfor normalen"

    trend = "for lite data"
    if len(prev4) >= 2:
        change = (recent - statistics.mean(prev4)) / sd
        improving = change > 0 if METRICS[metric][2] == "høyere" else change < 0
        trend = "stabil" if abs(change) < 0.5 else ("bedring" if improving else "forverring")

    out.update(
        {
            "normalområde": [_fmt(metric, lo), _fmt(metric, hi)],
            "snitt_siste_3_dager": _fmt(metric, recent),
            "snitt_siste_7_dager": _fmt(metric, statistics.mean(last7)),
            "dager_dårligere_enn_normalt_siste_3": worse_last3,
            "dager_dårligere_enn_normalt_siste_7": worse_last7,
            "trend_siste_7_dager": trend,
            "vurdering": status,
            "tydelig_utenfor_normalen": status == "dårligere enn normalt" and worse_last3 >= 2,
        }
    )
    return out


# Hvor bekymringsfull en vurdering er; ved uenighet brukes den høyeste.
SEVERITY = {"bedre enn normalt": 0, "innenfor normalen": 1, "dårligere enn normalt": 2}


def garmin_hrv(row: sqlite3.Row | None) -> dict | None:
    """Garmins HRV-status oversatt til samme skala som vår egen vurdering.

    Garmin vurderer 7-dagerssnittet mot sin baseline: BALANCED = innenfor,
    UNBALANCED = utenfor (over eller under), LOW/POOR = under. Statusen er
    allerede en flerdagerstrend, så LOW og POOR regnes som tydelig utenfor.
    """
    if row is None:
        return None
    status = (row["hrv_status"] or "").upper()
    if status in ("", "NONE"):  # oppstartsfase: ingen status ennå
        return None
    lo, hi, weekly = row["hrv_baseline_low"], row["hrv_baseline_high"], row["hrv_weekly_avg"]
    if status == "BALANCED":
        assessment = "innenfor normalen"
    elif status == "UNBALANCED":
        above = weekly is not None and hi is not None and weekly > hi
        # Over baseline regnes ikke som bedre; ukjent retning tolkes forsiktig.
        assessment = "innenfor normalen" if above else "dårligere enn normalt"
    elif status in ("LOW", "POOR"):
        assessment = "dårligere enn normalt"
    else:
        return None
    return {
        "status": status,
        "ukesnitt": weekly,
        "baseline": [lo, hi],
        "vurdering": assessment,
        "tydelig_utenfor_normalen": status in ("LOW", "POOR"),
    }


def combine_hrv(own: dict, garmin: dict | None) -> dict:
    """Slår sammen vår vurdering og Garmins status; den mest forsiktige vinner."""
    if garmin is None:
        return own
    combined = dict(own)
    own_assessment = own.get("vurdering")
    if own_assessment in SEVERITY:
        combined["vurdering"] = max(own_assessment, garmin["vurdering"], key=SEVERITY.get)
    else:  # for lite egne data: Garmins status alene
        combined["vurdering"] = garmin["vurdering"]
    combined["tydelig_utenfor_normalen"] = bool(own.get("tydelig_utenfor_normalen")) or garmin["tydelig_utenfor_normalen"]
    combined["garmin_hrv_status"] = garmin["status"]
    combined["garmin_hrv_ukesnitt"] = garmin["ukesnitt"]
    combined["garmin_hrv_baseline"] = garmin["baseline"]
    combined["vurderingsgrunnlag"] = "egen normal og Garmins HRV-status samlet; den mest forsiktige vurderingen gjelder"
    return combined


def overall_signal(trends: dict, established: bool, n_days: int, min_days: int) -> dict:
    clear = [k for k, t in trends.items() if t.get("tydelig_utenfor_normalen")]
    if not established:
        return {
            "nivå": "normalen_ikke_etablert",
            "forklaring": f"Bare {n_days} av {min_days} dager med data som trengs for et sikkert "
            "normalområde. Vurderingene er foreløpige; ikke trekk konklusjoner om avvik.",
            "tydelig_utenfor": clear,
        }
    if ("hrv" in clear and "hvilepuls" in clear) or len(clear) >= 3:
        return {
            "nivå": "tydelig_utenfor_normalen",
            "forklaring": "Flere målinger er tydelig dårligere enn normalt flere dager på rad. "
            "Si det rett ut og foreslå hvile.",
            "tydelig_utenfor": clear,
        }
    if clear:
        return {
            "nivå": "litt_utenfor_normalen",
            "forklaring": "Én måling har vært dårligere enn normalt flere dager. Nevn det rolig.",
            "tydelig_utenfor": clear,
        }
    return {"nivå": "normal_variasjon", "forklaring": "Alt innenfor din normale variasjon.", "tydelig_utenfor": []}


def _hm(ts: str) -> str:
    return ts[11:16]


def workouts(con: sqlite3.Connection, start: date, end: date, strength_minutes: int) -> list[dict]:
    rows = []
    for r in con.execute(
        "SELECT * FROM cardio_sessions WHERE date(start_time) BETWEEN ? AND ? ORDER BY start_time",
        (start.isoformat(), end.isoformat()),
    ):
        rows.append(
            {
                "dato": r["start_time"][:10],
                "start": r["start_time"],
                "slutt": r["stop_time"],
                "type": r["sport"],
                "varighet_min": round(r["duration_s"] / 60),
                "distanse_km": round(r["distance_m"] / 1000, 1) if r["distance_m"] else None,
                "snittpuls": r["hr_avg"],
                "makspuls": r["hr_max"],
                "kardiobelastning": r["cardio_load"],
                "treningseffekt": r["training_benefit"],
            }
        )
    for r in con.execute(
        "SELECT * FROM v_strength_session WHERE date(start_time) BETWEEN ? AND ? ORDER BY start_time",
        (start.isoformat(), end.isoformat()),
    ):
        est_end = datetime.fromisoformat(r["start_time"]) + timedelta(minutes=strength_minutes)
        rows.append(
            {
                "dato": r["start_time"][:10],
                "start": r["start_time"],
                "slutt": est_end.isoformat(timespec="seconds"),
                "slutt_er_anslått": True,
                "type": f"styrke ({r['workout_name']})",
                "sett": r["sets"],
                "harde_sett": r["hard_sets"],
                "tonnasje_kg": r["tonnage_kg"],
            }
        )
    rows.sort(key=lambda w: w["start"])
    return rows


def _merge_periods(times: list[datetime], max_gap: timedelta) -> list[tuple[datetime, datetime]]:
    periods = []
    for t in sorted(times):
        if periods and t - periods[-1][1] <= max_gap:
            periods[-1][1] = t
        else:
            periods.append([t, t])
    return [(a, b) for a, b in periods]


def stress_peaks(con: sqlite3.Connection, day: date, logged: list[dict], cfg: dict) -> dict:
    """Gårsdagens stresstopper, koblet til loggførte økter eller aktive perioder fra klokka."""
    rows = con.execute(
        "SELECT ts_local, stress_level, heart_rate FROM garmin_intraday WHERE date = ? ORDER BY ts_utc",
        (day.isoformat(),),
    ).fetchall()
    high = [datetime.fromisoformat(r["ts_local"]) for r in rows
            if r["stress_level"] is not None and r["stress_level"] >= cfg["stress_peak_threshold"]]
    level_at = {datetime.fromisoformat(r["ts_local"]): r["stress_level"] for r in rows}
    active_times = [datetime.fromisoformat(r["ts_local"]) for r in rows
                    if r["heart_rate"] is not None and r["heart_rate"] >= cfg["active_hr"]]
    active = [(a, b) for a, b in _merge_periods(active_times, timedelta(minutes=10))
              if b - a >= timedelta(minutes=10)]

    before = timedelta(minutes=30)
    after = timedelta(minutes=cfg["post_workout_minutes"])
    peaks = []
    for a, b in _merge_periods(high, timedelta(minutes=15)):
        peak = {
            "fra": a.strftime("%H:%M"),
            "til": b.strftime("%H:%M"),
            "høyeste_stress": max(v for t, v in level_at.items() if a <= t <= b and v is not None),
        }
        reason = None
        for w in logged:
            ws, we = datetime.fromisoformat(w["start"]), datetime.fromisoformat(w["slutt"])
            if a <= we + after and b >= ws - before:
                if a > we:
                    reason = f"{round((a - we).total_seconds() / 60)} min etter {w['type']} ({_hm(w['start'])}–{_hm(w['slutt'])})"
                elif b < ws:
                    reason = f"rett før {w['type']} ({_hm(w['start'])})"
                else:
                    reason = f"under {w['type']} ({_hm(w['start'])}–{_hm(w['slutt'])})"
                break
        if reason is None:
            for s, e in active:
                if a <= e + after and b >= s - timedelta(minutes=15):
                    reason = (f"under eller etter en aktiv periode klokka registrerte "
                              f"({s:%H:%M}–{e:%H:%M}, puls over {cfg['active_hr']}) – trolig trening "
                              "eller annen fysisk aktivitet, men ikke loggført")
                    break
        peak["sammenheng_med_trening"] = reason or "ingen kjent trening eller aktivitet i nærheten"
        peaks.append(peak)

    return {
        "dato": day.isoformat(),
        "terskel": cfg["stress_peak_threshold"],
        "topper": peaks,
        "aktive_perioder_fra_klokka": [f"{s:%H:%M}–{e:%H:%M}" for s, e in active],
    }


def _load_window(last_logged: str | None, today: date, cfg: dict) -> dict:
    """7-dagersvinduet og 4-ukersvinduet slutter på siste loggførte dag.

    Treningsloggene importeres for hånd, så dager etter siste økt kan mangle.
    Er loggen eldre enn `max_log_gap_days`, er signalet ukjent.
    """
    end = min(today, date.fromisoformat(last_logged))
    gap = (today - end).days
    return {
        "end": end,
        "start7": end - timedelta(days=6),
        "start28": end - timedelta(days=27),
        "gap": gap,
        "stale": gap > cfg["max_log_gap_days"],
    }


def _ratio_assessment(acute: float, weekly_avg: float, cfg: dict) -> tuple[float | None, str]:
    if not weekly_avg:
        return None, "ukjent"
    ratio = acute / weekly_avg
    if ratio > cfg["load_ratio_high"]:
        return round(ratio, 2), "høyere"
    if ratio < cfg["load_ratio_low"]:
        return round(ratio, 2), "lavere"
    return round(ratio, 2), "normal"


def _window_info(w: dict, last_logged: str) -> dict:
    info = {
        "periode": f"{w['start7']}–{w['end']}",
        "sammenlignet_med": f"ukesnitt {w['start28']}–{w['end']}",
        "logg_oppdatert_til": last_logged,
    }
    if w["stale"]:
        info["grunn_til_ukjent"] = (
            f"treningsloggen er ikke oppdatert de siste {w['gap']} dagene, så de siste 7 dagene er ufullstendige"
        )
    return info


def cardio_load_signal(con: sqlite3.Connection, today: date, cfg: dict) -> dict:
    """Polars kardiobelastning siste 7 dager mot ukesnittet siste 4 uker (inkl. denne uka)."""
    last = con.execute("SELECT MAX(date(start_time)) FROM cardio_sessions").fetchone()[0]
    if last is None:
        return {"vurdering": "ukjent", "grunn_til_ukjent": "ingen økter fra Polar"}
    w = _load_window(last, today, cfg)

    def total(start: date) -> tuple[float, int]:
        s, n = con.execute(
            "SELECT COALESCE(SUM(cardio_load), 0), COUNT(*) FROM cardio_sessions "
            "WHERE date(start_time) BETWEEN ? AND ?",
            (start.isoformat(), w["end"].isoformat()),
        ).fetchone()
        return s, n

    acute, n7 = total(w["start7"])
    weekly_avg = total(w["start28"])[0] / 4
    ratio, assessment = _ratio_assessment(acute, weekly_avg, cfg)
    return {
        "beskrivelse": "Kondisjonsbelastning (Polars cardio load: løping og judo)",
        **_window_info(w, last),
        "sum_siste_7_dager": round(acute, 1),
        "økter_siste_7_dager": n7,
        "ukesnitt_siste_4_uker": round(weekly_avg, 1),
        "forhold": ratio,
        "vurdering": "ukjent" if w["stale"] else assessment,
    }


def strength_load_signal(con: sqlite3.Connection, today: date, cfg: dict) -> dict:
    """Tonnasje og andel harde sett (RIR ≤ 3) siste 7 dager mot siste 4 uker."""
    last = con.execute("SELECT MAX(date(start_time)) FROM strength_sessions").fetchone()[0]
    if last is None:
        return {"vurdering": "ukjent", "grunn_til_ukjent": "ingen styrkeøkter"}
    w = _load_window(last, today, cfg)

    def totals(start: date) -> sqlite3.Row:
        return con.execute(
            "SELECT COALESCE(SUM(t.tonnage_kg), 0) AS tonnage, COUNT(DISTINCT s.id) AS sessions, "
            "COUNT(t.rir) AS with_rir, COALESCE(SUM(t.is_hard), 0) AS hard "
            "FROM strength_sets t JOIN strength_sessions s ON s.id = t.session_id "
            "WHERE date(s.start_time) BETWEEN ? AND ?",
            (start.isoformat(), w["end"].isoformat()),
        ).fetchone()

    a, c = totals(w["start7"]), totals(w["start28"])
    weekly_avg = c["tonnage"] / 4
    ratio, assessment = _ratio_assessment(a["tonnage"], weekly_avg, cfg)

    # Andel harde sett bare når det finnes nok sett med RIR i begge vinduene.
    min_sets = cfg["min_rir_sets"]
    share7 = a["hard"] / a["with_rir"] if a["with_rir"] >= min_sets else None
    share28 = c["hard"] / c["with_rir"] if c["with_rir"] >= min_sets else None
    if share7 is None or share28 is None:
        share_assessment = "ukjent (for få sett med RIR)"
    elif share7 - share28 > cfg["hard_share_diff"]:
        share_assessment = "høyere"
    elif share28 - share7 > cfg["hard_share_diff"]:
        share_assessment = "lavere"
    else:
        share_assessment = "normal"

    return {
        "beskrivelse": "Styrkebelastning (tonnasje, og andel harde sett med RIR ≤ 3)",
        **_window_info(w, last),
        "tonnasje_siste_7_dager_kg": round(a["tonnage"]),
        "økter_siste_7_dager": a["sessions"],
        "ukesnitt_tonnasje_siste_4_uker_kg": round(weekly_avg),
        "forhold": ratio,
        "vurdering": "ukjent" if w["stale"] else assessment,
        "andel_harde_sett_siste_7_dager": None if share7 is None else round(share7, 2),
        "andel_harde_sett_siste_4_uker": None if share28 is None else round(share28, 2),
        "sett_med_rir_siste_7_dager": a["with_rir"],
        "andel_harde_sett_vurdering": "ukjent" if w["stale"] else share_assessment,
    }


def collect(db_path, today: date, cfg: dict) -> dict:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        yesterday = today - timedelta(days=1)
        rows = {
            date.fromisoformat(r["date"]): r
            for r in con.execute(
                "SELECT * FROM daily_wellness WHERE date BETWEEN ? AND ?",
                ((today - timedelta(days=cfg["normal_window_days"])).isoformat(), today.isoformat()),
            )
        }

        trends = {}
        for metric, (_, _, _, _, kind, _) in METRICS.items():
            end = today if kind == "natt" else yesterday
            series = {d: v for d, r in rows.items() if d <= end and (v := _value(r, metric)) is not None}
            trends[metric] = assess_metric(metric, series, end, cfg)

        # Garmins HRV-status for natten som endte i dag (eller i går hvis ikke synkronisert).
        garmin = garmin_hrv(rows.get(today)) or garmin_hrv(rows.get(yesterday))
        trends["hrv"] = combine_hrv(trends["hrv"], garmin)

        n_days = min(trends["hrv"]["dager_med_data"], trends["hvilepuls"]["dager_med_data"])
        # Garmins baseline bygger på flere uker, så med status fra Garmin er HRV-normalen etablert.
        established = n_days >= cfg["min_days_for_normal"] or garmin is not None

        days = []
        for i in range(7, -1, -1):
            d = today - timedelta(days=i)
            r = rows.get(d)
            entry = {"dato": d.isoformat(), "ukedag": WEEKDAYS[d.weekday()]}
            if r is None:
                entry["mangler"] = "ingen data fra klokka"
            else:
                entry.update(
                    {
                        "natten_før": {
                            "leggetid": _hm(r["bedtime"]) if r["bedtime"] else None,
                            "våknetid": _hm(r["wake_time"]) if r["wake_time"] else None,
                            "søvn_timer": round(r["sleep_s"] / 3600, 2) if r["sleep_s"] else None,
                            "søvnscore": r["sleep_score"],
                            "hrv": r["hrv_night_avg"],
                            "body_battery_ved_oppvåkning": r["bb_at_wake"],
                        },
                        "dagen": {
                            "hvilepuls": r["resting_hr"],
                            "stress_snitt": r["stress_avg"],
                            "body_battery_høyest": r["bb_high"],
                            "body_battery_lavest": r["bb_low"],
                            "skritt": r["steps"],
                        },
                    }
                )
                if d == today:
                    entry["merknad"] = "i dag: bare natten er komplett, dagsverdiene er ikke ferdige"
            days.append(entry)

        logged = workouts(con, today - timedelta(days=7), today, cfg["strength_session_minutes"])
        last_polar = con.execute("SELECT MAX(date(start_time)) FROM cardio_sessions").fetchone()[0]
        last_strength = con.execute("SELECT MAX(date(start_time)) FROM strength_sessions").fetchone()[0]
        last_night = rows.get(today)

        return {
            "dato_i_dag": today.isoformat(),
            "ukedag_i_dag": WEEKDAYS[today.weekday()],
            "normalområde_etablert": established,
            "samlet_signal": overall_signal(trends, established, n_days, cfg["min_days_for_normal"]),
            "trender": trends,
            "dager": days,
            "økter_siste_7_dager": logged,
            # To separate signaler – ikke slått sammen til én samlet belastning.
            "belastning_siste_7_dager": {
                "kondisjon": cardio_load_signal(con, today, cfg),
                "styrke": strength_load_signal(con, today, cfg),
            },
            "treningslogg_oppdatert_til": {
                "løping_og_judo (Polar)": last_polar,
                "styrke (Gym Day)": last_strength,
                "merknad": "Økter etter disse datoene er ikke importert ennå – ukjent, ikke hviledager.",
            },
            "stresstopper_i_går": stress_peaks(con, yesterday, logged, cfg),
            "data_mangler": [] if last_night and last_night["sleep_s"] else ["søvn for i natt (klokka ikke synkronisert eller ikke brukt)"],
        }
    finally:
        con.close()
