"""AI-coachens morgenmelding: systemprompt, kall til OpenRouter, kontroll av
svaret og lagring av meldingen i data/raw/coach/ (lastes inn i coach_messages).
"""

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """\
Du er en personlig treningscoach. Hver morgen skriver du én kort melding til utøveren, \
som løper, trener styrke og driver med judo. Du får utøverens data som JSON: søvn, \
nattlig HRV, hvilepuls, stress og Body Battery for de siste dagene, utøverens eget \
normalområde, belastning, økter og gårsdagens stresstopper. Vurderingene i dataene er \
allerede regnet ut; din jobb er å formulere dem.

Slik skriver du:
- Skriv en sammenhengende, naturlig melding på god norsk bokmål, til «du», slik en \
erfaren trener ville snakket. Maks {max_sentences} setninger. Ren tekst uten overskrifter, \
punktlister eller emojier.
- Ikke gå gjennom hver måling. Velg de én eller to tingene som betyr mest i dag. Er alt \
normalt, si det kort.
- Start med det som går bra, og ta deretter det viktigste, rolig formulert.
- Ingen rå tall og ingen merkelapper: skriv «litt mer restitusjon i dag kan lønne seg», \
ikke «HRV 58, lavt». Ikke bruk fagord som HRV, Body Battery, score eller prosent; bruk \
vanlige ord som søvn, hvile, puls, overskudd og restitusjon. Klokkeslett («kl. 18») og \
minutter i et forslag («30 minutter») er greit.
- Snakk bare om trender over flere dager, sammenlignet med utøverens egen normal. En \
enkelt natt eller dag er ikke en trend.
- Avslutt med ett konkret forslag som bygger på dataene, skrevet som et forslag og ikke \
en ordre. Når alt er normalt, bygg videre på det som fungerer, eller knytt forslaget til \
treningen. Ikke foreslå å endre noe som allerede går bra.
- Vær rolig, men ærlig. Målet er ikke å skjule ting, men å unngå bekymring for normal \
variasjon.

Slik bruker du dataene:
- «samlet_signal.nivå» = «tydelig_utenfor_normalen»: si rett ut at kroppen viser tydelige \
tegn på at den trenger hvile, og foreslå hvile eller en helt rolig dag.
- «litt_utenfor_normalen»: nevn det rolig som noe å følge med på.
- «normal_variasjon»: ikke gjør noe nummer ut av små svingninger.
- «normalen_ikke_etablert»: ikke påstå at noe er over eller under normalen. Du kan nevne i \
en kort bisetning at du fortsatt blir kjent med utøverens normal, men ikke bruk en hel \
setning på det.
- Stresstopper: forklar en topp når den henger sammen med trening, f.eks. «Toppen kl. 18 \
var judo, det er bra belastning.» Kom den under eller etter en aktiv periode klokka \
registrerte, kan du si at den trolig kom av trening. Topper uten kjent årsak trenger du \
ikke nevne.
- «belastning_siste_7_dager» har to separate signaler, kondisjon og styrke. Ikke slå dem \
sammen. Høyere belastning er ikke negativt i seg selv; bruk det til å forklare hvordan \
kroppen responderer. Er en vurdering «ukjent», ikke nevn den. Lange trender over uker og \
måneder hører til den ukentlige oppsummeringen, ikke her.
- Økter etter datoene i «treningslogg_oppdatert_til» er ikke importert ennå. Ikke anta at \
utøveren har hvilt de dagene.
- HRV-vurderingen kan bygge på både den egne normalen og Garmins HRV-status. Bruk bare \
den samlede «vurdering». Ikke nevn Garmin, ulike kilder eller at de er uenige.
- Mangler data for i natt, si det kort i stedet for å gjette.

Tre eksempler på gode meldinger. De viser tone og lengde; ikke kopier formuleringene.

En normal dag:
«Søvnen og restitusjonen din ligger stabilt der de pleier, så kroppen er klar for en \
vanlig treningsdag. Skal du løpe i dag, kan du gjerne holde det rolige tempoet som har \
fungert så bra de siste ukene.»

En dag etter hard trening:
«Judoen i går satte tydelige spor, og toppen i stress rundt kl. 20 er akkurat det man \
venter etter en så hard økt. Natten ga deg god søvn, men kroppen bruker fortsatt litt tid \
på å hente seg inn. En rolig tur eller en lett styrkeøkt i dag kan være et godt valg.»

En dag der kroppen trenger hvile:
«Du har holdt jevnt og godt treningsvolum, og det skal du ha. De siste nettene viser \
kroppen likevel tydelige tegn på at den ikke henter seg inn: pulsen i hvile har ligget \
høyere enn vanlig samtidig som restitusjonen har vært svakere. Det kan lønne seg å ta en \
hviledag i dag og gi kroppen sjansen til å ta igjen det den trenger.»
"""

_ALLOWED_NUMBERS = [
    r"\bkl\.?\s*\d{1,2}(?:[:.]\d{2})?",  # kl. 18, kl 18:30
    r"\b\d{1,2}[:.]\d{2}\b",  # 18:30
    r"\b\d+\s*minutter\b",  # 30 minutter
]
_FORBIDDEN_TERMS = re.compile(r"\b(HRV|Body Battery|score|prosent|Garmin|baseline|uenig\w*)\b|%", re.I)


def system_prompt(max_sentences: int) -> str:
    return SYSTEM_PROMPT.format(max_sentences=max_sentences)


def prompt_version(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()[:8]


def count_sentences(text: str) -> int:
    cleaned = re.sub(r"\b(kl|f\.eks|bl\.a|ca|osv|ev|evt)\.", r"\1", text, flags=re.I)
    return len([s for s in re.split(r"(?<=[.!?])\s+", cleaned.strip()) if s])


def check_rules(text: str, max_sentences: int) -> list[str]:
    """Maskinell sjekk av reglene som kan sjekkes. Returnerer liste over brudd."""
    problems = []
    n = count_sentences(text)
    if n > max_sentences:
        problems.append(f"{n} setninger (maks {max_sentences})")
    stripped = text
    for pattern in _ALLOWED_NUMBERS:
        stripped = re.sub(pattern, "", stripped, flags=re.I)
    if re.search(r"\d", stripped):
        problems.append("inneholder tall (bare klokkeslett og minutter i forslaget er lov)")
    if m := _FORBIDDEN_TERMS.search(text):
        problems.append(f"bruker fagordet «{m.group(0)}»")
    if re.search(r"^\s*[-•*]|^#", text, re.M):
        problems.append("bruker liste eller overskrift")
    return problems


class CoachError(Exception):
    pass


def call_openrouter(api_key: str, model: str, messages: list[dict]) -> tuple[str, dict]:
    """Ett kall til OpenRouter. Prøver én gang til ved 429/5xx/nettverksfeil."""
    body = {
        "model": model,
        "messages": messages,
        # Modellen tenker før den svarer; romslig grense så svaret ikke kuttes.
        "max_tokens": 4000,
        "usage": {"include": True},
    }
    headers = {"Authorization": f"Bearer {api_key}", "X-Title": "ai-training-coach"}
    for attempt in (1, 2):
        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=120)
        except requests.RequestException as err:
            if attempt == 2:
                raise CoachError(f"Fikk ikke kontakt med OpenRouter: {err}") from err
            time.sleep(5)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == 2:
                raise CoachError(f"OpenRouter svarte {resp.status_code}: {resp.text[:300]}")
            time.sleep(5)
            continue
        break

    try:
        data = resp.json()
    except ValueError as err:
        raise CoachError(f"Uleselig svar fra OpenRouter ({resp.status_code}): {resp.text[:300]}") from err
    if not resp.ok or "error" in data:
        msg = (data.get("error") or {}).get("message", resp.text[:300])
        raise CoachError(f"OpenRouter svarte {resp.status_code}: {msg}")

    choice = data["choices"][0]
    text = (choice["message"].get("content") or "").strip()
    if not text:
        raise CoachError(f"Tomt svar fra modellen (finish_reason={choice.get('finish_reason')})")
    if choice.get("finish_reason") == "length":
        raise CoachError("Svaret ble kuttet (max_tokens nådd)")
    return text, data.get("usage") or {}


def generate(api_key: str, model: str, payload: dict, max_sentences: int) -> dict:
    """Lager meldingen. Bryter svaret reglene, bes modellen én gang om å skrive det om."""
    prompt = system_prompt(max_sentences)
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Dagens data:\n" + json.dumps(payload, ensure_ascii=False, indent=1)},
    ]
    text, usage = call_openrouter(api_key, model, messages)
    usages = [usage]
    first_draft, first_problems = text, check_rules(text, max_sentences)
    problems = first_problems
    if problems:
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": "Meldingen bryter reglene: " + "; ".join(problems)
             + ". Skriv den på nytt og svar bare med den nye meldingen."},
        ]
        text, usage = call_openrouter(api_key, model, messages)
        usages.append(usage)
        problems = check_rules(text, max_sentences)

    return {
        "message": text,
        "warnings": problems,
        "first_draft": first_draft,
        "first_draft_violations": first_problems,
        "attempts": len(usages),
        "system_prompt": prompt,
        "prompt_version": prompt_version(prompt),
        "input_tokens": sum(u.get("prompt_tokens", 0) for u in usages),
        "output_tokens": sum(u.get("completion_tokens", 0) for u in usages),
        "cost_usd": round(sum(u.get("cost", 0) or 0 for u in usages), 6),
    }


def save(coach_dir: Path, day: str, model: str, payload: dict, result: dict) -> Path:
    coach_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    record = {
        "date": day,
        "created_at": now.isoformat(timespec="seconds"),
        "model": model,
        "input": payload,
        **result,
    }
    path = coach_dir / f"{day}_{now:%H%M%S}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def latest_for_day(coach_dir: Path, day: str) -> dict | None:
    files = sorted(coach_dir.glob(f"{day}_*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def load_all(coach_dir: Path) -> list[dict]:
    """Alle lagrede meldinger som rader til coach_messages."""
    rows = []
    for path in sorted(coach_dir.glob("*.json")):
        r = json.loads(path.read_text(encoding="utf-8"))
        rows.append(
            {
                "date": r["date"],
                "created_at": r["created_at"],
                "model": r["model"],
                "prompt_version": r.get("prompt_version"),
                "message": r["message"],
                "warnings": "; ".join(r.get("warnings") or []) or None,
                "first_draft": r.get("first_draft"),  # mangler i meldinger fra før 2026-11-11
                "first_draft_violations": "; ".join(r.get("first_draft_violations") or []) or None,
                "attempts": r.get("attempts"),
                "input_tokens": r.get("input_tokens"),
                "output_tokens": r.get("output_tokens"),
                "cost_usd": r.get("cost_usd"),
                "input_json": json.dumps(r["input"], ensure_ascii=False),
            }
        )
    return rows
