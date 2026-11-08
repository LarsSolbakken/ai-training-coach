"""Leser rå Garmin-dagfiler (data/raw/garmin/days/*.json) til daily_wellness
(én rad per dato) og garmin_intraday (målinger gjennom døgnet).

Garmin-tidsstempler er i UTC og konverteres til lokal tid (config.toml).
Oppløsning for CIRQA Smart Band: stress og Body Battery hvert 3. min, puls
hvert 2. min, skritt summert per 15-minuttersintervall.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Garmin bruker negative stressverdier når stress ikke kan måles.
STRESS_STATUS = {-1: "insufficient_data", -2: "too_much_motion"}


def _utc_ms(ms: int | None) -> datetime | None:
    return datetime.fromtimestamp(ms / 1000, timezone.utc) if ms else None


def _utc_str(text: str | None) -> datetime | None:
    if not text:
        return None
    return datetime.fromisoformat(text.removesuffix(".0")).replace(tzinfo=timezone.utc)


def _local(dt: datetime | None, tz: ZoneInfo) -> str | None:
    return dt.astimezone(tz).replace(tzinfo=None).isoformat(timespec="seconds") if dt else None


def _stress(value: int | None) -> int | None:
    """Negative stressverdier er Garmins koder for «ikke målt»."""
    return value if value is not None and value >= 0 else None


def _iso_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def daily_row(day: dict, tz: ZoneInfo) -> dict:
    us = day.get("user_summary") or {}
    sleep_all = day.get("sleep") or {}
    sleep = sleep_all.get("dailySleepDTO") or {}
    score = (sleep.get("sleepScores") or {}).get("overall") or {}
    hrv = (day.get("hrv") or {}).get("hrvSummary") or {}
    baseline = hrv.get("baseline") or {}

    return {
        "date": day["date"],
        # Søvn – natten som endte denne datoen
        "bedtime": _local(_utc_ms(sleep.get("sleepStartTimestampGMT")), tz),
        "wake_time": _local(_utc_ms(sleep.get("sleepEndTimestampGMT")), tz),
        "sleep_s": sleep.get("sleepTimeSeconds"),
        "deep_sleep_s": sleep.get("deepSleepSeconds"),
        "light_sleep_s": sleep.get("lightSleepSeconds"),
        "rem_sleep_s": sleep.get("remSleepSeconds"),
        "awake_s": sleep.get("awakeSleepSeconds"),
        "awake_count": sleep.get("awakeCount"),
        "sleep_score": score.get("value"),
        "sleep_quality": score.get("qualifierKey"),
        "sleep_avg_hr": sleep.get("avgHeartRate"),
        "sleep_avg_stress": sleep.get("avgSleepStress"),
        "sleep_respiration": sleep.get("averageRespirationValue"),
        "skin_temp_dev_c": sleep_all.get("avgSkinTempDeviationC"),
        # Nattlig HRV
        "hrv_night_avg": hrv.get("lastNightAvg") or sleep_all.get("avgOvernightHrv"),
        "hrv_night_5min_high": hrv.get("lastNight5MinHigh"),
        "hrv_weekly_avg": hrv.get("weeklyAvg"),
        "hrv_baseline_low": baseline.get("balancedLow"),
        "hrv_baseline_high": baseline.get("balancedUpper"),
        "hrv_status": hrv.get("status") or sleep_all.get("hrvStatus"),
        # Puls
        "resting_hr": us.get("restingHeartRate") or sleep_all.get("restingHeartRate"),
        "resting_hr_7d_avg": us.get("lastSevenDaysAvgRestingHeartRate"),
        "min_hr": us.get("minHeartRate"),
        "max_hr": us.get("maxHeartRate"),
        # Aktivitet
        "steps": us.get("totalSteps"),
        "step_goal": us.get("dailyStepGoal"),
        "distance_m": us.get("totalDistanceMeters"),
        "active_kcal": us.get("activeKilocalories"),
        "total_kcal": us.get("totalKilocalories"),
        "intensity_min_moderate": us.get("moderateIntensityMinutes"),
        "intensity_min_vigorous": us.get("vigorousIntensityMinutes"),
        # Stress
        "stress_avg": _stress(us.get("averageStressLevel")),
        "stress_max": _stress(us.get("maxStressLevel")),
        "stress_rest_s": us.get("restStressDuration"),
        "stress_low_s": us.get("lowStressDuration"),
        "stress_medium_s": us.get("mediumStressDuration"),
        "stress_high_s": us.get("highStressDuration"),
        # Body Battery
        "bb_high": us.get("bodyBatteryHighestValue"),
        "bb_low": us.get("bodyBatteryLowestValue"),
        "bb_charged": us.get("bodyBatteryChargedValue"),
        "bb_drained": us.get("bodyBatteryDrainedValue"),
        "bb_at_wake": us.get("bodyBatteryAtWakeTime"),
        "bb_sleep_change": us.get("bodyBatteryDuringSleep") or sleep_all.get("bodyBatteryChange"),
        "respiration_waking_avg": us.get("avgWakingRespirationValue"),
        "fetched_at": day.get("fetched_at"),
    }


def intraday_rows(day: dict, tz: ZoneInfo) -> list[dict]:
    """Slår sammen stress, Body Battery, puls og skritt på felles tidsstempel."""
    rows: dict[datetime, dict] = {}

    def row(ts: datetime) -> dict:
        if ts not in rows:
            rows[ts] = {
                "ts_utc": _iso_utc(ts),
                "ts_local": _local(ts, tz),
                "date": day["date"],
                "stress_level": None,
                "stress_status": None,
                "body_battery": None,
                "bb_status": None,
                "heart_rate": None,
                "steps": None,
                "activity_level": None,
            }
        return rows[ts]

    stress = day.get("stress") or {}
    for ts, level in stress.get("stressValuesArray") or []:
        r = row(_utc_ms(ts))
        if level is not None and level >= 0:
            r["stress_level"], r["stress_status"] = level, "measured"
        else:
            r["stress_status"] = STRESS_STATUS.get(level, "unknown")
    for ts, status, level, *_ in stress.get("bodyBatteryValuesArray") or []:
        r = row(_utc_ms(ts))
        r["body_battery"], r["bb_status"] = level, status

    for ts, hr in (day.get("heart_rates") or {}).get("heartRateValues") or []:
        if hr is not None:
            row(_utc_ms(ts))["heart_rate"] = hr

    # Skritt kommer som 15-minuttersintervaller; lagres på intervallets starttid.
    for interval in day.get("steps") or []:
        r = row(_utc_str(interval["startGMT"]))
        r["steps"] = interval.get("steps")
        r["activity_level"] = interval.get("primaryActivityLevel")

    return [rows[ts] for ts in sorted(rows)]


def parse(days_dir: Path, tz_name: str):
    tz = ZoneInfo(tz_name)
    daily, intraday = [], []
    for path in sorted(days_dir.glob("*.json")):
        day = json.loads(path.read_text(encoding="utf-8"))
        daily.append(daily_row(day, tz))
        intraday += intraday_rows(day, tz)
    return daily, intraday
