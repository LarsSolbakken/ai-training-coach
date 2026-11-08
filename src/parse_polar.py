"""Leser Polar-eksporten (training-session_*.json) til økter, pulssoner,
sekundmålinger og GPS-rute.

Regler (bekreftet av brukeren):
- sport.id er fasit for sport; feltet «name» kan være feil og lagres bare som referanse.
- GPS-data (rute, koordinater, og distanse/fart som kommer fra GPS) ignoreres for judo.
- Puls 0 i sekundmålingene er utfall og lagres som NULL.
"""

import json
from pathlib import Path

SPORTS = {1: "løping", 115: "judo"}
SPORTS_WITHOUT_GPS = {"judo"}


def _stat(exercise: dict, kind: str) -> dict:
    for s in exercise.get("statistics", {}).get("statistics", []):
        if s["type"] == f"STATISTICS_TYPE_{kind}":
            return s
    return {}


def _samples(exercise: dict) -> dict[str, tuple[int, list]]:
    return {
        s["type"]: (s.get("intervalMillis", 1000), s.get("values") or [])
        for s in exercise.get("samples", {}).get("samples", [])
    }


def _round(value, ndigits=1):
    return None if value is None else round(value, ndigits)


def parse_session(path: Path):
    d = json.loads(path.read_text(encoding="utf-8"))
    ex = d["exercises"][0]
    sid = d["identifier"]["id"]
    sport_id = int(d["sport"]["id"])
    sport = SPORTS.get(sport_id, f"sport_{sport_id}")
    use_gps = sport not in SPORTS_WITHOUT_GPS

    samples = _samples(ex)
    hr_interval, hr_values = samples.get("HEART_RATE", (1000, []))
    hr_clean = [v if v else None for v in hr_values]
    hr_valid = [v for v in hr_clean if v is not None]

    hr_stat = _stat(ex, "HEART_RATE")
    speed_stat = _stat(ex, "SPEED") if use_gps else {}

    session = {
        "id": sid,
        "sport": sport,
        "sport_id": sport_id,
        "name_raw": d.get("name"),
        "start_time": d["startTime"],
        "stop_time": d.get("stopTime"),
        "tz_offset_min": d.get("timezoneOffsetMinutes"),
        "duration_s": round(d["durationMillis"] / 1000),
        "distance_m": _round(d.get("distanceMeters")) if use_gps else None,
        "calories": d.get("calories"),
        # Sammendraget mangler av og til puls; da regnes den fra sekundmålingene.
        "hr_avg": d.get("hrAvg")
        or hr_stat.get("avg")
        or (round(sum(hr_valid) / len(hr_valid)) if hr_valid else None),
        "hr_min": hr_stat.get("min") or (min(hr_valid) if hr_valid else None),
        "hr_max": d.get("hrMax") or hr_stat.get("max") or (max(hr_valid) if hr_valid else None),
        "speed_avg_kmh": _round(speed_stat.get("avg"), 2),
        "speed_max_kmh": _round(speed_stat.get("max"), 2),
        "cardio_load": _round(d.get("trainingLoadReport", {}).get("cardioLoad"), 1),
        "training_benefit": (d.get("trainingBenefit") or "").removeprefix("TRAINING_BENEFIT_") or None,
        "recovery_h": _round(int(d["recoveryTimeMillis"]) / 3_600_000, 1)
        if d.get("recoveryTimeMillis")
        else None,
        "running_index": ex.get("runningIndex") if sport == "løping" else None,
    }

    zones = []
    for z in ex.get("zones", []):
        if z["type"] == "ZONE_TYPE_HEART_RATE":
            for i, zone in enumerate(z["zones"], 1):
                zones.append(
                    {
                        "session_id": sid,
                        "zone": i,
                        "lower_bpm": zone.get("lowerLimit"),
                        "upper_bpm": zone.get("higherLimit"),
                        "seconds": round(zone.get("inZone", 0) / 1000),
                    }
                )

    speed_interval, speed_values = samples.get("SPEED", (1000, [])) if use_gps else (1000, [])
    dist_interval, dist_values = samples.get("DISTANCE", (1000, [])) if use_gps else (1000, [])
    series: dict[float, dict] = {}
    for interval, values, key in (
        (hr_interval, hr_clean, "hr_bpm"),
        (speed_interval, speed_values, "speed_kmh"),
        (dist_interval, dist_values, "distance_m"),
    ):
        for i, v in enumerate(values):
            t = i * interval / 1000
            series.setdefault(t, {"session_id": sid, "t_s": t, "hr_bpm": None, "speed_kmh": None, "distance_m": None})
            series[t][key] = _round(v, 2) if key != "hr_bpm" else v
    sample_rows = [series[t] for t in sorted(series)]

    route = []
    if use_gps:
        for wp in ex.get("routes", {}).get("route", {}).get("wayPoints", []):
            route.append(
                {
                    "session_id": sid,
                    "t_s": wp.get("elapsedMillis", 0) / 1000,
                    "lat": wp.get("latitude"),
                    "lon": wp.get("longitude"),
                }
            )

    return session, zones, sample_rows, route


def parse(polar_dir: Path):
    """Returnerer (sessions, zones, samples, route) for alle økter i eksporten(e)."""
    sessions, zones, samples, route = [], [], [], []
    seen = set()
    for path in sorted(polar_dir.glob("*/training-session_*.json")):
        s, z, smp, r = parse_session(path)
        if s["id"] in seen:  # samme økt i flere eksporter
            continue
        seen.add(s["id"])
        sessions.append(s)
        zones += z
        samples += smp
        route += r
    sessions.sort(key=lambda s: s["start_time"])
    return sessions, zones, samples, route
