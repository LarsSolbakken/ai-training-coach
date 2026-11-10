"""Leser styrkeloggen (data/raw/styrke/gym-day.csv) til økter og sett.

Regler (bekreftet av brukeren):
- Notatfeltet er ikke quotet; rader med flere felt enn overskriften får alt
  etter kolonne 9 slått sammen til ett notat.
- Settene renummereres per økt og øvelse slik at de starter på 1.
- Tom eller 0 vekt på kroppsvektøvelser = bare kroppsvekt; vekt > 0 = ekstravekt.
- Tonnasje = (kroppsvekt + ekstravekt) × reps for kroppsvektøvelser,
  ellers vekt × reps.
- Estimert 1RM bare for øvelser med ytre last og sett der reps + RIR ≤ 5
  (uten RIR: reps ≤ 5). Formel velges i config.toml (brzycki: vekt × 36 /
  (37 − reps), eller epley); med RIR brukes reps + RIR som reps i formelen.
- RIR/RPE hentes fra notatet; RPE regnes om med RIR = 10 − RPE.
  «RIR 10+» lagres som 10. Hardt sett = RIR ≤ 3.
"""

import csv
import re
from collections import defaultdict
from pathlib import Path

N_COLUMNS = 10
HARD_SET_MAX_RIR = 3
E1RM_MAX_REPS = 5

_NUM = r"(\d+(?:[.,]\d+)?)"
_RANGE_SEP = r"\s*(?:-|–|til|og)\s*"
# «RIR 2», «rir2», «RIR: 1,5», «RIR 10+», «RPE 8-9»
_LABEL_FIRST = re.compile(rf"\b(RIR|RPE)\s*[:=]?\s*{_NUM}(?:{_RANGE_SEP}{_NUM})?\s*(\+)?", re.I)
# «9.5 RPE», «8 og 9 rpe», «2 RIR»
_NUMBER_FIRST = re.compile(rf"(?<![\d.,]){_NUM}(?:{_RANGE_SEP}{_NUM})?\s*(\+)?\s*(RIR|RPE)\b", re.I)


def _num(text: str | None) -> float | None:
    return float(text.replace(",", ".")) if text else None


def parse_effort(note: str) -> tuple[float | None, float | None]:
    """Henter (rir, rpe) slik de står i notatet. Et intervall gir midtpunktet."""
    found: dict[str, float] = {}
    for m in _LABEL_FIRST.finditer(note):
        label, lo, hi = m.group(1).upper(), _num(m.group(2)), _num(m.group(3))
        found.setdefault(label, (lo + hi) / 2 if hi is not None else lo)
    for m in _NUMBER_FIRST.finditer(note):
        lo, hi, label = _num(m.group(1)), _num(m.group(2)), m.group(4).upper()
        found.setdefault(label, (lo + hi) / 2 if hi is not None else lo)
    rir, rpe = found.get("RIR"), found.get("RPE")
    if rir is not None and rir >= 10:  # «RIR 10+» og alt over: lagres som 10
        rir = 10.0
    if rpe is not None and not 1 <= rpe <= 10:
        rpe = None
    return rir, rpe


def effective_rir(rir: float | None, rpe: float | None) -> tuple[float | None, str | None]:
    if rir is not None:
        return rir, "RIR"
    if rpe is not None:
        return 10 - rpe, "RPE"
    return None, None


def epley(weight: float, reps: float) -> float | None:
    return weight if reps == 1 else weight * (1 + reps / 30)


def brzycki(weight: float, reps: float) -> float | None:
    return weight * 36 / (37 - reps) if reps < 37 else None


E1RM_FORMULAS = {"epley": epley, "brzycki": brzycki}


def estimate_1rm(formula: str, weight: float, reps: int, rir: float | None) -> float | None:
    """Estimert 1RM. Med RIR brukes reps + RIR (reps til failure) i formelen."""
    value = E1RM_FORMULAS[formula](weight, reps + (rir or 0))
    return None if value is None else round(value, 1)


def read_rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        if len(header) != N_COLUMNS:
            raise ValueError(f"Uventet overskrift i {path}: {header}")
        rows = []
        for row in reader:
            if not row:
                continue
            if len(row) > N_COLUMNS:  # komma i notatet
                row = row[: N_COLUMNS - 1] + [",".join(row[N_COLUMNS - 1 :])]
            rows.append(row)
    return rows


def parse(path: Path, bodyweight_kg: float, bodyweight_exercises: set[str], e1rm_formula: str):
    """Returnerer (sessions, sets) som lister av dicts klare for databasen."""
    if e1rm_formula not in E1RM_FORMULAS:
        raise ValueError(
            f"Ukjent e1rm_formula {e1rm_formula!r} i config.toml – velg en av {sorted(E1RM_FORMULAS)}"
        )
    rows = read_rows(path)

    sessions: dict[str, dict] = {}
    raw_sets = []
    for order, (when, workout, exercise, set_no, weight, reps, *_rest, notes) in enumerate(rows):
        start_time = when.strip().replace(" ", "T")
        if start_time not in sessions:
            sessions[start_time] = {
                "id": len(sessions) + 1,
                "start_time": start_time,
                "workout_name": workout.strip(),
            }
        raw_sets.append(
            {
                "order": order,
                "session_id": sessions[start_time]["id"],
                "exercise": exercise.strip(),
                "set_no_raw": int(set_no),
                "weight": float(weight) if weight.strip() else 0.0,
                "reps": int(float(reps)),
                "notes": notes.strip() or None,
            }
        )

    # Renummerer settene per (økt, øvelse) i opprinnelig rekkefølge.
    groups = defaultdict(list)
    for s in raw_sets:
        groups[(s["session_id"], s["exercise"])].append(s)
    for group in groups.values():
        for i, s in enumerate(sorted(group, key=lambda s: (s["set_no_raw"], s["order"])), 1):
            s["set_no"] = i

    sets = []
    for s in raw_sets:
        is_bw = s["exercise"] in bodyweight_exercises
        load = s["weight"]
        total_load = bodyweight_kg + load if is_bw else load
        rir_written, rpe = parse_effort(s["notes"] or "")
        rir, rir_source = effective_rir(rir_written, rpe)
        e1rm = (
            estimate_1rm(e1rm_formula, load, s["reps"], rir)
            if not is_bw and load > 0 and s["reps"] >= 1 and s["reps"] + (rir or 0) <= E1RM_MAX_REPS
            else None
        )
        sets.append(
            {
                "session_id": s["session_id"],
                "exercise": s["exercise"],
                "set_no": s["set_no"],
                "set_no_raw": s["set_no_raw"],
                "load_kg": load,
                "total_load_kg": total_load,
                "reps": s["reps"],
                "tonnage_kg": round(total_load * s["reps"], 2),
                "e1rm_kg": e1rm,
                "rir": rir,
                "rpe": rpe,
                "rir_source": rir_source,
                "is_hard": None if rir is None else int(rir <= HARD_SET_MAX_RIR),
                "notes": s["notes"],
            }
        )
    return list(sessions.values()), sets
