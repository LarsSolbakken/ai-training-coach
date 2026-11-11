"""Rolig dashbord over treningsdataene i data/training.db.

Startes med  .venv/bin/python -m src.dashboard  (bare på 127.0.0.1 – inneholder helsedata).

Designregler: ingen daglige poengsummer på forsiden, ingen røde/grønne
varselfarger, og trender over uker i stedet for enkeltdager.
"""

import sqlite3
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # Streamlit kjører filen som et skript
    sys.path.insert(0, str(ROOT))

from src.coach_data import METRICS  # noqa: E402
from src.config import DB_PATH, load_config  # noqa: E402

# --- Farger og typografi ------------------------------------------------------
# Kategoriske farger validert for fargeblindhet mot lys flate (#fcfcfb), i fast
# rekkefølge: blå, oransje, fiolett, magenta. Ingen rød eller grønn.
SURFACE, PAGE = "#fcfcfb", "#f9f9f7"
INK, INK_2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
BLUE, ORANGE, VIOLET, MAGENTA = "#2a78d6", "#eb6834", "#6250d6", "#e87ba4"
BAND = "#cde2fb"  # lys blå for normalområdet
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

LIFTS = {  # øvelse i loggen -> (navn, farge)
    "Barbell Bench Press": ("Benkpress", BLUE),
    "Barbell Squat": ("Knebøy", ORANGE),
    "Barbell Deadlifts": ("Markløft", VIOLET),
    "Trap Bar Deadlift": ("Trap bar", MAGENTA),
}
SESSION_TYPES = {"Styrke": BLUE, "Løping": ORANGE, "Judo": VIOLET}
DETAIL_METRICS = {  # nøkkel i coach_data.METRICS -> (kolonne, tittel, enhet)
    "søvnlengde": ("sleep_h", "Søvn", "timer"),
    "hvilepuls": ("resting_hr", "Hvilepuls", "slag/min"),
    "hrv": ("hrv_night_avg", "Nattlig HRV", "ms"),
}
MONTH_LABEL = (
    "['jan','feb','mar','apr','mai','jun','jul','aug','sep','okt','nov','des']"
    "[month(datum.value)] + ' ' + year(datum.value)"
)

st.set_page_config(page_title="Treningsdagbok", page_icon="🏃", layout="centered")
st.markdown(
    f"""
    <style>
      .block-container {{ padding-top: 2.5rem; max-width: 860px; }}
      .coach-card {{
        background: {SURFACE}; border: 1px solid rgba(11,11,11,0.10); border-radius: 12px;
        padding: 1.25rem 1.5rem; font-size: 1.08rem; line-height: 1.6; color: {INK};
      }}
      .coach-meta {{ color: {MUTED}; font-size: 0.85rem; margin-top: 0.6rem; }}
    </style>
    """,
    unsafe_allow_html=True,
)


# --- Data -------------------------------------------------------------------
@st.cache_data(ttl=60)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)  # bare lesing
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


def styled(chart: alt.Chart, height: int) -> alt.Chart:
    return (
        chart.properties(height=height, background=SURFACE, padding={"left": 8, "right": 16, "top": 8, "bottom": 8})
        .configure_view(stroke=None)
        .configure_axis(
            labelColor=MUTED, titleColor=INK_2, gridColor=GRID, domainColor=AXIS, tickColor=AXIS,
            labelFont=FONT, titleFont=FONT, labelFontSize=11, titleFontSize=12, titleFontWeight="normal",
        )
        .configure_axisX(grid=False)
        .configure_legend(
            orient="top", title=None, labelColor=INK_2, labelFont=FONT, labelFontSize=12, symbolStrokeWidth=3,
        )
        .configure_text(font=FONT)
    )


def time_axis(title: str | None = None, step_months: int = 3) -> alt.Axis:
    return alt.Axis(title=title, labelExpr=MONTH_LABEL, tickCount={"interval": "month", "step": step_months})


# --- Forside ------------------------------------------------------------------
def coach_message() -> None:
    df = query(
        "SELECT date, created_at, message FROM coach_messages ORDER BY date DESC, created_at DESC LIMIT 1"
    )
    if df.empty:
        st.info("Ingen melding fra coachen ennå. Kjør `.venv/bin/python -m src.morning`.")
        return
    row = df.iloc[0]
    day = pd.Timestamp(row["date"])
    is_today = day.date() == pd.Timestamp.now().date()
    meta = "Dagens melding" if is_today else f"Siste melding, {day:%d.%m.%Y} – kjør morgenkommandoen for dagens"
    st.markdown(
        f'<div class="coach-card">{row["message"]}<div class="coach-meta">{meta}</div></div>',
        unsafe_allow_html=True,
    )


def strength_progress() -> None:
    st.subheader("Styrkeprogresjon")
    st.caption(
        "Linje: beste estimerte 1RM de siste 4 ukene. Prikker: beste per uke. "
        "Brzycki, sett med reps + RIR ≤ 5."
    )
    names = list(LIFTS)
    df = query(
        f"SELECT week_start, exercise, best_e1rm_kg FROM v_best_e1rm "
        f"WHERE exercise IN ({','.join('?' * len(names))}) ORDER BY week_start",
        tuple(names),
    )
    if df.empty:
        st.info("Ingen styrkedata.")
        return
    df["Øvelse"] = df["exercise"].map(lambda e: LIFTS[e][0])
    df["Uke"] = pd.to_datetime(df["week_start"])
    df["uke_kg"] = df["best_e1rm_kg"].round(1)
    df = df[["Øvelse", "Uke", "uke_kg"]].sort_values(["Øvelse", "Uke"])
    # Beste verdi i uka og de tre foregående ukene (28 dager bakover fra ukestart).
    df["kg"] = (
        df.set_index("Uke").groupby("Øvelse")["uke_kg"].rolling("28D").max().round(1).to_numpy()
    )
    # Bryt linjen ved opphold over 6 uker, så den ikke later som det finnes data der.
    gaps = []
    for lift, g in df.groupby("Øvelse"):
        for prev, nxt in zip(g["Uke"], g["Uke"].iloc[1:]):
            if nxt - prev > pd.Timedelta(weeks=6):
                gaps.append({"Øvelse": lift, "Uke": prev + (nxt - prev) / 2, "kg": None})
    line_df = pd.concat([df[["Øvelse", "Uke", "kg"]], pd.DataFrame(gaps)]).sort_values(["Øvelse", "Uke"])
    domain = [v[0] for v in LIFTS.values()]
    lift_scale = alt.Scale(domain=domain, range=[v[1] for v in LIFTS.values()])
    # symbolOpacity=1: forklaringen skal ikke arve de svake ukeprikkene.
    color = alt.Color("Øvelse:N", scale=lift_scale, legend=alt.Legend(symbolType="stroke", symbolOpacity=1))
    x = alt.X("Uke:T", axis=time_axis(step_months=6))
    y = alt.Y("kg:Q", title="kg", scale=alt.Scale(zero=False))
    base = alt.Chart(df)

    # Ukeverdiene som svake prikker bak linjen.
    weekly = base.mark_circle(size=18, opacity=0.25).encode(x=x, y=alt.Y("uke_kg:Q"), color=color)
    lines = alt.Chart(line_df).mark_line(
        strokeWidth=2, strokeCap="round", strokeJoin="round", invalid="break-paths-show-domains"
    ).encode(x=x, y=y, color=color)
    # Hover: loddrett linje nærmeste uke, med alle løft i verktøytipset.
    hover = alt.selection_point(fields=["Uke"], nearest=True, on="pointerover", empty=False, clear="pointerout")
    rule = (
        base.transform_pivot("Øvelse", value="kg", groupby=["Uke"])
        .mark_rule(color=AXIS, strokeWidth=1)
        .encode(
            x=x,
            opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=[alt.Tooltip("Uke:T", title="Uke fra", format="%d.%m.%Y")]
            + [alt.Tooltip(f"{n}:Q", title=f"{n}, beste siste 4 uker", format=".1f") for n in domain],
        )
        .add_params(hover)
    )
    points = base.mark_circle(size=64, stroke=SURFACE, strokeWidth=2, opacity=1).encode(
        x=x, y="kg:Q", color=color,
    ).transform_filter(hover)
    # Enkeltuker mellom to opphold har ingen linje å tegnes på – vis dem som prikker.
    marked = line_df.assign(
        alone=line_df.groupby("Øvelse")["kg"].transform(lambda s: s.notna() & s.shift().isna() & s.shift(-1).isna())
    )
    isolated = alt.Chart(marked[marked["alone"]]).mark_circle(size=30, opacity=1).encode(x=x, y="kg:Q", color=color)
    # Direkte etiketter ved siste verdi (tekst i blekkfarge, ikke seriefarge) – bare for
    # løft som er trent de siste 8 ukene; avsluttede løft står i forklaringen øverst.
    last = df.groupby("Øvelse").tail(1)
    last = last[last["Uke"] >= df["Uke"].max() - pd.Timedelta(weeks=8)]
    labels = alt.Chart(last).mark_text(align="left", dx=8, fontSize=11, color=INK_2).encode(
        x="Uke:T", y="kg:Q", text="Øvelse:N",
    )
    st.altair_chart(
        styled(alt.layer(weekly, lines, isolated, rule, points, labels), 300), theme=None, width="stretch"
    )
    with st.expander("Vis som tabell"):
        table = df.pivot_table(index="Uke", columns="Øvelse", values=["kg", "uke_kg"])
        table = table.rename(columns={"kg": "Beste siste 4 uker", "uke_kg": "Beste i uka"}, level=0)
        table = table.swaplevel(axis=1).sort_index(axis=1, level=0).sort_index(ascending=False)
        table.index = table.index.strftime("%d.%m.%Y")
        st.dataframe(table, width="stretch")


def weekly_sessions() -> None:
    st.subheader("Trening per uke")
    span = st.segmented_control(
        "Periode", ["6 mnd", "12 mnd", "Alt"], default="12 mnd", label_visibility="collapsed"
    ) or "12 mnd"
    strength = query(
        "SELECT date(start_time,'weekday 0','-6 days') AS week_start, 'Styrke' AS Type, COUNT(*) AS n "
        "FROM strength_sessions GROUP BY 1"
    )
    cardio = query(
        "SELECT date(start_time,'weekday 0','-6 days') AS week_start, "
        "CASE sport WHEN 'løping' THEN 'Løping' WHEN 'judo' THEN 'Judo' ELSE sport END AS Type, "
        "COUNT(*) AS n FROM cardio_sessions GROUP BY 1, 2"
    )
    df = pd.concat([strength, cardio])
    df["Uke"] = pd.to_datetime(df["week_start"])
    df["Uke_slutt"] = df["Uke"] + pd.Timedelta(days=7)
    if span != "Alt":
        months = 6 if span == "6 mnd" else 12
        df = df[df["Uke"] >= pd.Timestamp.now().normalize() - pd.DateOffset(months=months)]
    order = list(SESSION_TYPES)
    df["rekkefølge"] = df["Type"].map(order.index)

    chart = alt.Chart(df).mark_bar(stroke=SURFACE, strokeWidth=2).encode(
        x=alt.X("Uke:T", axis=time_axis(step_months=1 if span == "6 mnd" else 2 if span == "12 mnd" else 6)),
        x2="Uke_slutt:T",
        y=alt.Y("sum(n):Q", title="Økter", axis=alt.Axis(tickMinStep=1, format="d")),
        color=alt.Color("Type:N", scale=alt.Scale(domain=order, range=list(SESSION_TYPES.values()))),
        order=alt.Order("rekkefølge:Q"),
        tooltip=[
            alt.Tooltip("Uke:T", title="Uke fra", format="%d.%m.%Y"),
            alt.Tooltip("Type:N", title="Type"),
            alt.Tooltip("sum(n):Q", title="Økter"),
        ],
    )
    st.altair_chart(styled(chart, 240), theme=None, width="stretch")
    first_cardio = cardio["week_start"].min()
    if first_cardio:
        st.caption(f"Løping og judo fra Polar finnes fra uka {pd.Timestamp(first_cardio):%d.%m.%Y}.")
    with st.expander("Vis som tabell"):
        table = df.pivot_table(index="Uke", columns="Type", values="n", aggfunc="sum", fill_value=0)
        table = table.reindex(columns=[c for c in order if c in table.columns]).sort_index(ascending=False)
        table.index = table.index.strftime("%d.%m.%Y")
        st.dataframe(table, width="stretch")


# --- Detaljer ------------------------------------------------------------------
def detail_metric(key: str, daily: pd.DataFrame, cfg: dict) -> None:
    col, title, unit = DETAIL_METRICS[key]
    min_spread = METRICS[key][5]
    s = daily[col]
    # Samme normalområde som coachen: snitt ± 1 standardavvik over siste 60 dager.
    window = f"{cfg['normal_window_days']}D"
    mean = s.rolling(window, min_periods=3).mean()
    sd = s.rolling(window, min_periods=3).std().clip(lower=min_spread)
    df = pd.DataFrame(
        {
            "Dato": s.index,
            "snitt7": s.rolling("7D", min_periods=3).mean(),
            "lav": mean - sd,
            "høy": mean + sd,
        }
    ).dropna(subset=["snitt7"])
    n_days = int(s.notna().sum())

    st.subheader(title)
    if df.empty:
        st.caption("For lite data ennå.")
        return
    established = n_days >= cfg["min_days_for_normal"]
    st.caption(
        f"7-dagers snitt ({unit}). Skyggelagt: ditt normalområde"
        + ("" if established else f" – foreløpig, {n_days} av {cfg['min_days_for_normal']} dager med data")
    )
    span_days = (df["Dato"].max() - df["Dato"].min()).days
    ticks = {"interval": "day", "step": 1} if span_days < 21 else {"interval": "week", "step": 1 if span_days < 120 else 4}
    x = alt.X("Dato:T", axis=alt.Axis(title=None, format="%d.%m", tickCount=ticks))
    base = alt.Chart(df)
    band = base.mark_area(color=BAND, opacity=0.7).encode(
        x=x, y=alt.Y("lav:Q", title=unit, scale=alt.Scale(zero=False)), y2="høy:Q",
    )
    line = base.mark_line(color=BLUE, strokeWidth=2, strokeCap="round").encode(x=x, y="snitt7:Q")
    hover = alt.selection_point(fields=["Dato"], nearest=True, on="pointerover", empty=False, clear="pointerout")
    rule = base.mark_rule(color=AXIS).encode(
        x=x,
        opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        tooltip=[
            alt.Tooltip("Dato:T", format="%d.%m.%Y"),
            alt.Tooltip("snitt7:Q", title="7-dagers snitt", format=".1f"),
            alt.Tooltip("lav:Q", title="Normal fra", format=".1f"),
            alt.Tooltip("høy:Q", title="Normal til", format=".1f"),
        ],
    ).add_params(hover)
    dot = base.mark_circle(size=64, color=BLUE, stroke=SURFACE, strokeWidth=2, opacity=1).encode(
        x=x, y="snitt7:Q"
    ).transform_filter(hover)
    st.altair_chart(styled(alt.layer(band, line, rule, dot), 200), theme=None, width="stretch")


def details() -> None:
    cfg = load_config()["coach"]
    daily = query(
        "SELECT date, sleep_s / 3600.0 AS sleep_h, resting_hr, hrv_night_avg FROM daily_wellness ORDER BY date"
    )
    if daily.empty:
        st.info("Ingen data fra klokka ennå.")
        return
    daily["date"] = pd.to_datetime(daily["date"])
    daily = daily.set_index("date").asfreq("D")  # tomme dager som hull, ikke som null
    st.caption("Trender over tid, ikke enkeltnetter. Normalområdet er det samme som coachen bruker.")
    for key in DETAIL_METRICS:
        detail_metric(key, daily, cfg)


# --- Side ---------------------------------------------------------------------
if not DB_PATH.exists():
    st.error("Fant ikke data/training.db. Kjør `.venv/bin/python -m src.update` først.")
    st.stop()

st.title("Treningsdagbok")
default_tab = "Detaljer" if st.query_params.get("fane") == "detaljer" else "Forside"
front, detail = st.tabs(["Forside", "Detaljer"], default=default_tab)
with front:
    coach_message()
    st.write("")
    strength_progress()
    weekly_sessions()
with detail:
    details()
