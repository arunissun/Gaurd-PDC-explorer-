"""PDC Exposure Explorer: Streamlit dashboard over the shared guard_pdc package.

Live Montandon API only (read-only POST /search). The local-export code path
stays in the package but is not connected here. Nothing is retrieved on page
load; a retrieval runs only when Retrieve is pressed.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
from typing import Any

import pandas as pd
import streamlit as st

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import components as ui  # noqa: E402

from guard_pdc import figures as F  # noqa: E402
from guard_pdc import maps as M  # noqa: E402
from guard_pdc import diagnostics as D  # noqa: E402
from guard_pdc.analysis import (  # noqa: E402
    age_profile, age_shares, build_analysis_frames, combined_country_events, country_event_counts, coverage_by_year, event_summary,
)
from guard_pdc.api import PdcApiProvider  # noqa: E402
from guard_pdc.config import MontandonConfig  # noqa: E402
from guard_pdc.countries import COUNTRY_NAMES, country_label, country_name  # noqa: E402
from guard_pdc.exports import build_export_files, public_frame, public_text  # noqa: E402
from guard_pdc.models import MAX_PERIOD_YEARS, MAX_YEAR, MIN_YEAR, MONTH_ABBREVIATIONS, QueryResult, QuerySpec, parse_country_codes  # noqa: E402
from guard_pdc.service import retrieve_country_group  # noqa: E402
from guard_pdc.taxonomy import AGE_BAND_CATEGORIES, CAPITAL_UNIT_NOTE, DEFAULT_MEASURES, HAZARD_GROUPS, MEASURE_CATEGORY, MEASURES, measures_query  # noqa: E402
from guard_pdc.theme import PLOTLY_CONFIG, compact, hazard_color  # noqa: E402

APP_TITLE = "PDC Exposure Explorer"
MAP_LAYERS = {"points": "Events", "clusters": "Clusters", "density": "Density"}
MAX_COUNTRIES = 5
SUMMARY_MEASURE_OPTIONS = ("people", "households", "schools", "hospitals", "capital")
MEASURE_SHORT = {"people": "People", "households": "Households", "schools": "Schools", "hospitals": "Hospitals", "capital": "Capital"}
PHASES = {"pdc-events": (0, "events"), "pdc-hazards": (1, "hazard alerts"), "pdc-impacts": (2, "exposure values")}
COUNTRY_OPTIONS = tuple(sorted(COUNTRY_NAMES, key=lambda code: COUNTRY_NAMES[code]))
COUNTRY_COUNT_NOTE = (
    "An event that lists several of the selected countries is counted in each of them, so country counts overlap and "
    "must not be added. Counts are distinct PDC events, never summed exposure."
)
BULLETIN_HELP = (
    "PDC tsunami bulletins are alerts for a whole warning region; their point is the warning-centre location "
    "and their exposure covers the full region. They are hidden by default."
)


# ------------------------------------------------------------------ query contract

def query_from_values(
    *,
    analysis_mode: str,
    country_code: str | None,
    year: int,
    months: tuple[int, ...],
    hazard_labels: tuple[str, ...],
    impact_types: tuple[str, ...],
    categories: tuple[str, ...],
    include_zero_values: bool = True,
    include_missing_geometry: bool = True,
    end_year: int | None = None,
) -> QuerySpec:
    codes = tuple(code for label, values in HAZARD_GROUPS if label in hazard_labels for code in values)
    annual = analysis_mode == "annual_country_overview"
    return QuerySpec(
        analysis_mode=analysis_mode,
        source_mode="api_only",
        country_code=country_code,
        year=year,
        end_year=None if annual else end_year,
        months=months,
        hazard_codes=codes,
        impact_types=("affected_total",) if annual else impact_types,
        categories=("people",) if annual else categories,
        include_zero_values=include_zero_values,
        include_missing_geometry=include_missing_geometry,
        refresh_api_cache=True,
    )


def queries_from_values(**values: Any) -> tuple[QuerySpec, ...]:
    raw = values.get("country_code") or ""
    countries = parse_country_codes(raw if isinstance(raw, str) else ",".join(raw))
    if countries:
        return tuple(query_from_values(**{**values, "country_code": country}) for country in countries)
    return (query_from_values(**{**values, "country_code": None}),)


# ------------------------------------------------------------------ cached helpers

def available_years(country: str | None) -> tuple[int, ...]:
    """Years with at least one PDC event (one limit=1 search per year).

    A country is checked only against years that have PDC data worldwide
    (cached separately), so a new country needs a handful of requests.
    """

    provider = PdcApiProvider(MontandonConfig.from_env(), max_workers=8)
    candidates = range(MIN_YEAR, MAX_YEAR + 1) if country is None else _available_years_cached(None)
    found = provider.years_with_events(candidates, country=country)
    return tuple(year for year, present in sorted(found.items()) if present)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _available_years_cached(country: str | None) -> tuple[int, ...]:
    return available_years(country)


@st.cache_resource(show_spinner=False)
def _footprint_hosts() -> tuple[str, ...]:
    return MontandonConfig.from_env(require_token=False).footprint_hosts


@st.cache_data(ttl=3600, max_entries=400, show_spinner=False)
def _alert_areas(url: str | None, point: tuple[float, float] | None) -> M.AlertAreas:
    return M.fetch_alert_areas(url, allowed_hosts=_footprint_hosts(), point=point)


def _point(row: pd.Series) -> tuple[float, float] | None:
    return (float(row["longitude"]), float(row["latitude"])) if bool(row.get("valid_point")) else None


def _view(result: QueryResult) -> dict[str, Any]:
    frames = build_analysis_frames(result)
    return {"frames": frames, "summary": event_summary(frames), "coverage": coverage_by_year(frames)}


def _is_bulletin(summary: pd.DataFrame) -> pd.Series:
    return summary["caveat"].fillna("").str.startswith("Tsunami bulletin")


def _measures_in(query: QuerySpec) -> list[str]:
    retrieved = set(query.categories)
    return [measure for measure in SUMMARY_MEASURE_OPTIONS if MEASURE_CATEGORY[measure] in retrieved]


def _has_age_bands(query: QuerySpec) -> bool:
    return set(AGE_BAND_CATEGORIES) <= set(query.categories)


# ------------------------------------------------------------------ sidebar

def _years_for(countries: list[str]) -> tuple[list[int], str | None]:
    years: set[int] = set()
    try:
        for country in countries:
            years.update(_available_years_cached(country))
    except Exception as error:  # UI boundary: fall back to every allowed year.
        return list(range(MIN_YEAR, MAX_YEAR + 1)), f"Could not check which years have PDC data ({type(error).__name__}); all years are offered."
    return sorted(years), None


def sidebar() -> tuple[tuple[QuerySpec, ...] | None, bool]:
    with st.sidebar:
        ui.sidebar_brand(APP_TITLE, "IFRC Montandon · PDC data")
        st.caption("Data source: Live Montandon API only (read-only). Nothing is retrieved until you press Retrieve.")
        ui.step(1, "Where")
        countries = st.multiselect(
            "Countries", COUNTRY_OPTIONS, default=["PHL"], format_func=country_label, key="countries",
            max_selections=MAX_COUNTRIES, placeholder="Choose up to 5 countries",
            help="Each country is retrieved separately; events shared by several countries appear in each.",
        )
        problems: list[str] = []
        start = end = None
        ui.step(2, "When")
        if not countries:
            problems.append("Choose at least one country.")
        else:
            with st.spinner("Checking which years have PDC data…"):
                years, years_warning = _years_for(countries)
            if years_warning:
                st.warning(years_warning)
            if not years:
                problems.append("Montandon has no PDC events for the chosen countries.")
            elif len(years) == 1:
                start = end = years[0]
                st.markdown(f"**Years:** {years[0]} (the only year with PDC data)")
            else:
                last = years[-1]
                first = min(year for year in years if year >= last - (MAX_PERIOD_YEARS - 1))
                start, end = st.select_slider(
                    "Years", options=years, value=(first, last), key="years-" + "-".join(sorted(countries)),
                    help=f"Only years with PDC events are offered. A period covers at most {MAX_PERIOD_YEARS} consecutive years.",
                )
                st.caption("PDC data exists for: " + ", ".join(str(year) for year in years))
                if end - start + 1 > MAX_PERIOD_YEARS:
                    problems.append(f"Choose at most {MAX_PERIOD_YEARS} consecutive years ({start}–{end} is {end - start + 1}).")
                gaps = [year for year in range(start, end + 1) if year not in years]
                if gaps and end - start + 1 <= MAX_PERIOD_YEARS:
                    st.caption("No PDC events in " + ", ".join(str(year) for year in gaps) + " for these countries.")
        months = st.pills(
            "Months", list(range(1, 13)), format_func=lambda month: MONTH_ABBREVIATIONS[month - 1], selection_mode="multi",
            default=list(range(1, 13)), key="months", help="Months of event start, applied to every selected year.",
        )
        if not months:
            problems.append("Choose at least one month.")
        ui.step(3, "What")
        hazards = st.pills("Hazards", [label for label, _ in HAZARD_GROUPS], selection_mode="multi", default=[], key="hazards")
        st.caption("No hazard selected = all hazards.")
        measures = st.pills(
            "Measures", [*DEFAULT_MEASURES, "age_groups"], format_func=lambda measure: MEASURES[measure][0], selection_mode="multi",
            default=list(DEFAULT_MEASURES), key="measures",
            help="People is always included. Age groups add 14 age bands per event (much more data); PDC derives them from a national age structure.",
        )
        selected_measures = ["people", *[measure for measure in measures if measure != "people"]]
        queries = None
        if not problems:
            impact_types, categories = measures_query(selected_measures)
            try:
                queries = queries_from_values(
                    analysis_mode="country_detail", country_code=tuple(countries), year=int(start), end_year=int(end),
                    months=tuple(sorted(months)), hazard_labels=tuple(hazards), impact_types=impact_types, categories=categories,
                )
            except Exception as error:  # validation message shown to the user
                problems.append(public_text(str(error)))
        for problem in problems:
            st.error(problem)
        clicked = st.button("Retrieve data", type="primary", width="stretch", disabled=queries is None)
        state = st.session_state.get("pdc")
        if state:
            loaded = state["queries"]
            names = ", ".join(country_name(query.country_code) for query in loaded)
            st.caption(f"Loaded: {names} · {loaded[0].period_label}")
            if queries is not None and tuple(query.fingerprint for query in queries) != tuple(query.fingerprint for query in loaded):
                st.info("Settings changed. Press Retrieve to load them.")
    return queries, clicked


# ------------------------------------------------------------------ retrieval

def run_retrieval(queries: tuple[QuerySpec, ...], previous: dict[str, QueryResult] | None = None) -> None:
    order = {query.country_code: index for index, query in enumerate(queries)}
    started = time.perf_counter()
    with st.status("Retrieving from the Montandon API…", expanded=True) as status:
        bar = st.progress(0.0, text="Starting…")

        def on_progress(key: str, event: Any) -> None:
            phase, label = PHASES.get(event.collection, (0, event.collection))
            within = event.completed_windows / max(event.total_windows, 1)
            fraction = (order.get(key, 0) * 3 + phase + within) / (3 * len(queries))
            bar.progress(min(max(fraction, 0.0), 1.0), text=f"{country_name(key)} · {label} · {event.completed_windows} of {event.total_windows} months")

        try:
            results = retrieve_country_group(queries, on_progress=on_progress, previous=previous)
        except Exception as error:  # UI boundary: keep the last good result.
            status.update(label="Retrieval failed", state="error", expanded=True)
            st.error(f"Retrieval failed: {type(error).__name__}: {public_text(str(error))}")
            return
        bar.progress(1.0, text="Preparing charts…")
        views = {key: _view(result) for key, result in results.items()}
        elapsed = time.perf_counter() - started
        complete = all(result.complete for result in results.values())
        status.update(label=f"Retrieved in {elapsed:.0f} s" + ("" if complete else " · some parts failed"), state="complete" if complete else "error", expanded=False)
    st.session_state["pdc"] = {
        "queries": queries, "results": results, "views": views,
        "retrieved_at": datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"), "seconds": elapsed,
    }
    st.session_state.pop("selected_event", None)
    st.session_state.pop("overlay", None)


# ------------------------------------------------------------------ selection

def _select_from_chart(key: str) -> None:
    state = st.session_state.get(key)
    try:
        points = state["selection"]["points"]
    except (KeyError, TypeError):
        return
    for point in points:
        data = point.get("customdata")
        if isinstance(data, (list, tuple)) and data:
            st.session_state["selected_event"] = str(data[0])
            return


def _select_from_table(key: str, order_key: str) -> None:
    state = st.session_state.get(key)
    try:
        rows = state["selection"]["rows"]
    except (KeyError, TypeError):
        return
    order = st.session_state.get(order_key, [])
    if rows and rows[0] < len(order):
        st.session_state["selected_event"] = order[rows[0]]


def _chart(fig: Any, key: str, *, selectable: bool = False) -> None:
    if selectable:
        st.plotly_chart(fig, theme=None, config=PLOTLY_CONFIG, key=key, on_select=lambda: _select_from_chart(key), selection_mode="points")
    else:
        st.plotly_chart(fig, theme=None, config=PLOTLY_CONFIG, key=key)


# ------------------------------------------------------------------ tabs

def tab_overview(summary: pd.DataFrame, query: QuerySpec, measure: str) -> None:
    noun = F.MEASURE_NOUNS[measure]
    multi_year = len(query.years) > 1
    head, switch = st.columns((5, 2))
    with head:
        ui.section(
            "How many events, and how big, over time?",
            f"Events per month by event start{', across ' + str(len(query.years)) + ' years' if multi_year else ''}.",
        )
    with switch:
        split = st.segmented_control(
            "Split events by", ["size", "hazard"], format_func={"size": f"Peak {noun}", "hazard": "Hazard"}.get, default="size", key="overview-split",
        ) or "size"
    period = _period_slider(query, "overview-period")
    _chart(F.fig_impact_timeseries(summary, measure, query.years, query.months, split=split, period=period), "overview-timeseries")
    ui.note(f"Each event is counted once, in the month it started. Exposure is not added across events: repeated PDC alerts can cover the same {noun}.")
    ui.section("When in the year do events start?", "Distinct events per month. Darker = more events.")
    _chart(F.fig_seasonality(summary, query.years, query.months), "overview-seasonality")
    ui.section(f"Which hazards expose the most {noun} per event?", f"Peak {noun} exposed per event. Box = middle half of events, line = median, whiskers = smallest to largest.")
    _chart(F.fig_hazard_profile(summary, measure, height=420), "overview-profile")
    if multi_year:
        ui.note("PDC coverage in Montandon differs strongly between years (see Data quality). Differences between years can reflect coverage, not only hazard activity.")


def _map_caption(summary: pd.DataFrame, show_bulletins: bool, show_all: bool) -> str:
    notes = M.map_notes(summary, show_bulletins=show_bulletins, show_all=show_all)
    parts = [f"{notes['plotted']} events on the map."]
    if notes["no_point"]:
        parts.append(f"{notes['no_point']} without a valid PDC point are listed in the Events tab.")
    if notes["outside_view"]:
        parts.append(f"{notes['outside_view']} far-away events are outside the view (turn on 'Fit all events').")
    if notes["regional"]:
        parts.append(f"{notes['regional']} multi-country alerts are drawn faded.")
    return " ".join(parts) + " " + M.MAP_NOTE


def _event_lines(row: pd.Series, measures: list[str]) -> list[str]:
    started = F._date(row["event_date"])
    updates = int(row["snapshot_count"])
    lines = [f"{row['pdc_hazard_type'] or row['hazard_group']} · started {started} · {updates} PDC update{'s' if updates != 1 else ''}"]
    lines.append("Countries: " + ", ".join(country_name(code) for code in row["countries"]))
    values = [f"{MEASURE_SHORT[measure]}: {compact(row[f'{measure}_peak'])}" for measure in measures if not pd.isna(row[f"{measure}_peak"])]
    if values:
        lines.append("Peak " + " · ".join(values))
    return lines


def _event_chips(row: pd.Series) -> str:
    chips = ui.chip(row["hazard_group"], background=hazard_color(row["hazard_group"]), color="#FFFFFF") + ui.alert_chip(row["alert_level_max"])
    if row["multi_country"]:
        chips += ui.chip(f"{row['n_countries']} countries", background="#EEF0F3", color="#4A5565")
    if row["caveat"]:
        chips += ui.chip("Caveat", background="#FFF4DB", color="#7A5200")
    return f"<div style='margin:2px 0 6px 0'>{chips}</div>"


def tab_map(summary: pd.DataFrame, measure: str, show_bulletins: bool, selected: str | None, measures: list[str], country: str) -> None:
    controls = st.columns((2, 2, 4))
    layer = controls[0].segmented_control("Layer", list(MAP_LAYERS), format_func=MAP_LAYERS.get, default="points", key="map-layer") or "points"
    show_all = controls[1].toggle("Fit all events", value=False, help="By default the view fits the central 90% of events so that a few distant alerts do not shrink the map.")
    left, right = st.columns((3, 1.15), gap="medium")
    with left:
        fig = M.fig_event_map(summary, measure, layer=layer, show_bulletins=show_bulletins, show_all=show_all, selected_family=selected)
        _chart(fig, "map-events", selectable=layer != "density")
        if layer != "density":
            ui.size_legend(f"{F.MEASURE_LABELS[measure]} (peak)", M.size_legend_items(summary, measure, show_bulletins=show_bulletins))
        ui.note(_map_caption(summary, show_bulletins, show_all) + (" " + M.CLUSTER_NOTE if layer == "clusters" else ""))
    with right:
        ui.section("Selected event", "Click a dot to select an event.")
        if selected is not None and selected in set(summary["family_key"]):
            row = summary[summary["family_key"] == selected].iloc[0]
            ui.event_card(str(row["title"]), _event_lines(row, measures), _event_chips(row), "Open the Event detail tab for its PDC alert area and history.",
                          swatch=hazard_color(row["hazard_group"]))
        else:
            ui.note("No event selected.")

    st.divider()
    ui.section("Compare PDC alert areas", "Overlay the latest PDC alert areas of the largest events of one hazard. Overlaps darken.")
    with_assets = summary.dropna(subset=["footprint_url"])
    groups = [group for group in F.HAZARD_GROUP_ORDER if group in set(with_assets["hazard_group"])]
    if not groups:
        ui.note("No event in this view advertises a PDC alert-area file.")
        return
    columns = st.columns((2, 2, 1.2))
    group = columns[0].selectbox("Hazard", groups, key="overlay-group")
    count = columns[1].slider("Largest events", 5, 50, 20, step=5, key="overlay-count")
    columns[2].markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    load = columns[2].button("Show alert areas", key="overlay-load", width="stretch")
    overlay_key = (country, group, count, measure, show_bulletins)
    if load:
        rows = with_assets[with_assets["hazard_group"] == group].sort_values(f"{measure}_peak", ascending=False, na_position="last").head(count)
        with st.spinner(f"Loading {len(rows)} PDC alert-area files…"):
            with ThreadPoolExecutor(max_workers=6) as pool:
                areas = list(pool.map(lambda item: _alert_areas(item[1]["footprint_url"], _point(item[1])), rows.iterrows()))
        st.session_state["overlay"] = (overlay_key, [(row, area) for (_, row), area in zip(rows.iterrows(), areas, strict=True)])
    stored = st.session_state.get("overlay")
    if stored and stored[0] == overlay_key:
        pairs = stored[1]
        _chart(M.fig_alert_area_overlay(pairs), "map-overlay")
        failed = sum(area.status != "available" for _, area in pairs)
        ui.note(f"{len(pairs) - failed} of {len(pairs)} alert areas shown." + (f" {failed} could not be used (missing, inaccessible or invalid)." if failed else "") + " " + M.ALERT_AREA_NOTE)


def _events_table(summary: pd.DataFrame, measure: str) -> tuple[pd.DataFrame, list[str]]:
    ordered = summary.sort_values(f"{measure}_peak", ascending=False, na_position="last")
    return pd.DataFrame({
        "Event": ordered["title"].fillna("Untitled"),
        "Hazard": ordered["hazard_group"],
        "Start": ordered["event_date"].dt.tz_convert(None).dt.date if not ordered.empty else [],
        f"Peak {MEASURE_SHORT[measure].lower()}": ordered[f"{measure}_peak"],
        f"Latest {MEASURE_SHORT[measure].lower()}": ordered[f"{measure}_latest"],
        "Highest PDC alert": ordered["alert_level_max"].fillna("–").str.title(),
        "PDC updates": ordered["snapshot_count"],
        "Countries": ordered["countries"].map(lambda codes: ", ".join(country_name(code) for code in codes)),
        "Caveat": ordered["caveat"].fillna(""),
    }).reset_index(drop=True), list(ordered["family_key"])


def tab_events(summary: pd.DataFrame, measure: str) -> None:
    noun = F.MEASURE_NOUNS[measure]
    ui.section("Largest events", f"Top 15 events by peak {noun} exposed. Click a bar to select the event.")
    _chart(F.fig_top_events(summary, measure), "events-top", selectable=True)
    ui.section("Every event over time", f"One row per hazard; dot size = peak {noun} exposed. Click a dot to select it.")
    _chart(F.fig_timeline(summary, measure), "events-timeline", selectable=True)
    ui.section("All events", "Sorted by peak value. Click a row to select an event.")
    table, order = _events_table(summary, measure)
    st.session_state["events-order"] = order
    value_format = "compact"
    st.dataframe(
        table, hide_index=True, width="stretch", height=420, key="events-table", on_select=lambda: _select_from_table("events-table", "events-order"),
        selection_mode="single-row",
        column_config={
            "Event": st.column_config.TextColumn(width="medium"),
            "Hazard": st.column_config.TextColumn(width="small"),
            f"Peak {MEASURE_SHORT[measure].lower()}": st.column_config.NumberColumn(format=value_format),
            f"Latest {MEASURE_SHORT[measure].lower()}": st.column_config.NumberColumn(format=value_format),
            "Start": st.column_config.DateColumn(format="DD MMM YYYY"),
        },
    )


def _period_slider(query: QuerySpec, key: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """Plain month-range slider for multi-year periods (None for one year)."""

    if len(query.years) < 2:
        return None
    months_all = list(pd.period_range(f"{query.years[0]}-01", f"{query.years[-1]}-12", freq="M").to_timestamp())
    first, last = st.select_slider(
        "Period shown", options=months_all, value=(months_all[0], months_all[-1]), format_func=lambda month: month.strftime("%b %Y"),
        key=f"{key}-{query.years[0]}-{query.years[-1]}",
    )
    return first, last


def tab_exposure(summary: pd.DataFrame, query: QuerySpec, measure: str, measures: list[str]) -> None:
    noun = F.MEASURE_NOUNS[measure]
    ui.section(
        "How much did the largest event of each month expose?",
        "One chart per measure, month by month. Each point is the largest single event of that month (hover for its name); "
        "values are never added across events.",
    )
    period = _period_slider(query, "exposure-period")
    for index in range(0, len(measures), 2):
        columns = st.columns(2, gap="medium")
        for column, item in zip(columns, measures[index: index + 2], strict=False):
            with column:
                st.markdown(f"**{F.MEASURE_LABELS[item]}** · largest event of the month")
                _chart(F.fig_measure_timeseries(summary, item, query.years, query.months, period=period), f"exposure-ts-{item}")
    if "capital" in measures:
        ui.note(f"Capital: {CAPITAL_UNIT_NOTE}. Values are shown as published; no currency is assumed.")
    left, right = st.columns(2, gap="large")
    with left:
        ui.section(f"How many events exposed at least X {noun}?", "Read across: e.g. how many events exposed at least 1M. Peak value per event; log scales.")
        _chart(F.fig_exceedance(summary, measure), "exposure-exceedance")
    with right:
        ui.section("How large are events?", f"Number of events by peak {noun} exposed.")
        kind = st.segmented_control("View", ["histogram", "ecdf"], format_func={"histogram": "Histogram", "ecdf": "Cumulative share"}.get, default="histogram", key="exposure-kind", label_visibility="collapsed") or "histogram"
        _chart(F.fig_distribution(summary, measure, kind), "exposure-distribution")
    for item in [item for item in ("schools", "hospitals") if item in measures]:
        stats = F.infrastructure_stats(summary, item)
        ui.section(f"Events exposing the most {item}", f"{stats['with_any']} of {len(summary)} events exposed at least one.")
        _chart(F.fig_infrastructure(summary, item), f"exposure-top-{item}")


def _age_line(frames: Any, family_key: str) -> str | None:
    profile, info = age_profile(frames, family_key)
    if profile.empty or not info.get("band_sum"):
        return None
    values = dict(zip(profile["impact_category"], profile["value"], strict=False))
    under5 = values.get("children_0_4")
    under15 = sum(values.get(category, 0) for category in ("children_0_4", "children_5_9", "children_10_14"))
    over65 = values.get("elderly")
    people = info.get("people")
    head = f"Of {compact(people)} people exposed: " if people else "Age bands: "
    return head + f"{compact(under5)} under 5 · {compact(under15)} under 15 · {compact(over65)} aged 65+."


def _retrieve_age_detail(query: QuerySpec, row: pd.Series) -> None:
    event_query = QuerySpec(
        analysis_mode="country_detail", source_mode="api_only", country_code=query.country_code, year=int(row["year"]),
        months=(int(row["month"]),), hazard_codes=query.hazard_codes, impact_types=("affected_total",),
        categories=("people", *AGE_BAND_CATEGORIES), refresh_api_cache=True,
    )
    with st.spinner("Retrieving age groups for this event's month…"):
        try:
            result = retrieve_country_group((event_query,))[query.country_code]
        except Exception as error:  # UI boundary
            st.error(f"Could not retrieve age groups: {type(error).__name__}: {public_text(str(error))}")
            return
    st.session_state.setdefault("age-detail", {})[row["family_key"]] = build_analysis_frames(result)


def tab_event_detail(summary: pd.DataFrame, frames: Any, query: QuerySpec, measure: str, measures: list[str]) -> None:
    if summary.empty:
        ui.note("No events in this view.")
        return
    ordered = summary.sort_values(f"{measure}_peak", ascending=False, na_position="last")
    keys = list(ordered["family_key"])
    labels = {row.family_key: f"{F._short(row.title, 60)} · {F._date(row.event_date)} · {compact(getattr(row, f'{measure}_peak'))}" for row in ordered.itertuples()}
    if st.session_state.get("selected_event") not in labels:
        st.session_state["selected_event"] = keys[0]
    family_key = st.selectbox("Event", keys, format_func=labels.get, key="selected_event", help="Sorted by peak value. Selecting a dot, bar or table row elsewhere also selects it here.")
    row = summary[summary["family_key"] == family_key].iloc[0]
    color = hazard_color(row["hazard_group"])
    lines = _event_lines(row, [])
    lines.append(f"PDC updates from {F._date(row['first_snapshot_time'])} to {F._date(row['last_snapshot_time'])}")
    ui.event_card(str(row["title"]), lines, _event_chips(row), row["caveat"] or "", swatch=color)
    if row["report_url"]:
        st.link_button("Open the PDC event report", row["report_url"])
    cards = []
    for item in measures:
        peak, latest = row[f"{item}_peak"], row[f"{item}_latest"]
        status = row[f"{item}_status"]
        if status == "present_positive" or status == "present_zero":
            cards.append((F.MEASURE_LABELS[item].replace(" (unit not stated)", ""), compact(peak), f"Latest {compact(latest)} · {int(row[f'{item}_changes'])} changes"))
        else:
            cards.append((F.MEASURE_LABELS[item].replace(" (unit not stated)", ""), "–", {"missing": "Not published", "unavailable": "No numeric value", "conflicting": "Conflicting values", "not_retrieved": "Not retrieved"}.get(status, status)))
    ui.kpis(cards)
    if "capital" in measures and isinstance(row["description_currency_quote"], str):
        ui.note(f"{CAPITAL_UNIT_NOTE}. PDC's description says: “{row['description_currency_quote']}”")

    ui.section("PDC alert area", M.HAZARD_MAP_NOTES.get(row["hazard_group"], ""))
    with st.spinner("Loading the PDC alert area…"):
        footprint = _alert_areas(row["footprint_url"], _point(row))
    _chart(M.fig_alert_area_map(row, footprint, height=520), f"detail-area-{family_key}")
    if footprint.status == "available":
        latest = footprint.latest
        area = f"{compact(latest.area_km2)} km²" if latest is not None and latest.area_km2 is not None else "not computed"
        inside = {True: "inside", False: "outside", None: "not checked against"}[footprint.point_inside]
        ui.note(f"{len(footprint.areas)} PDC alert-area version(s); latest area {area}; the PDC point is {inside} the latest area. " + " ".join(footprint.notes) + " " + M.ALERT_AREA_NOTE)
    else:
        ui.note(f"Alert area not shown: {footprint.message} The map shows the PDC point only.")
    ui.section("Who was exposed, by age?")
    age_frames = frames if _has_age_bands(query) else st.session_state.get("age-detail", {}).get(family_key)
    line = _age_line(age_frames, family_key) if age_frames is not None else None
    if line:
        st.markdown(line)
        ui.note("PDC splits exposed people by a fixed national age structure, so age shares are the same for every event in a country; the counts show scale.")
    elif age_frames is not None:
        ui.note("PDC published no age bands for this event.")
    elif st.button("Get age groups for this event", key=f"age-{family_key}"):
        _retrieve_age_detail(query, row)
        st.rerun()


def _select_country_from_chart(key: str, keys: list[str]) -> None:
    """A click on a selected country makes it the country shown in the other tabs."""

    state = st.session_state.get(key)
    try:
        points = state["selection"]["points"]
    except (KeyError, TypeError):
        return
    for point in points:
        data = point.get("customdata")
        code = data[0] if isinstance(data, (list, tuple)) and data else point.get("location")
        if code in keys:
            st.session_state["view-country"] = code
            return


def tab_compare(views: dict[str, dict[str, Any]], keys: list[str], query: QuerySpec, measure: str, show_bulletins: bool) -> None:
    summaries = {}
    for key in keys:
        summary = views[key]["summary"]
        summaries[key] = summary if show_bulletins else summary[~_is_bulletin(summary)]
    combined = combined_country_events(summaries)
    counts = country_event_counts(combined)
    counts = counts[counts["country_code"].isin(keys)].reset_index(drop=True)
    ui.section("Where are the selected countries' events?", "Distinct PDC events per selected country. Darker = more events. Click a country to show it in the other tabs.")
    st.plotly_chart(M.fig_country_choropleth(counts, period=query.period_label, fit=True, height=380), theme=None, config=PLOTLY_CONFIG,
                    key="compare-choropleth", on_select=lambda: _select_country_from_chart("compare-choropleth", keys), selection_mode="points")
    ui.note(COUNTRY_COUNT_NOTE)
    head, switch = st.columns((5, 2), vertical_alignment="bottom")
    with head:
        ui.section("All selected countries on one map", f"{len(combined):,} distinct events; an event shared by several selected countries appears once.")
    with switch:
        layer = st.segmented_control("Layer", list(MAP_LAYERS), format_func=MAP_LAYERS.get, default="clusters", key="compare-map-layer") or "clusters"
    _chart(M.fig_event_map(combined, measure, layer=layer, show_bulletins=show_bulletins, show_all=True, uniform_size=9, zoom_offset=-0.6, height=520), "compare-events")
    ui.note("Dots have one size here: PDC exposure values are per country, so a shared event has a different value in each. "
            "Each country's Map tab shows sizes and values." + (" " + M.CLUSTER_NOTE if layer == "clusters" else ""))
    ui.section("Events per period in each country", "Distinct events, stacked by hazard, on a shared scale.")
    _chart(F.fig_compare_per_year(summaries, query.years), "compare-periods")
    left, right = st.columns(2, gap="large")
    with left:
        ui.section("Which hazards dominate in each country?", "Distinct events per hazard. Hover for the median peak value.")
        _chart(F.fig_compare_hazard_mix(summaries, measure), "compare-mix")
    with right:
        noun = F.MEASURE_NOUNS[measure]
        ui.section(f"How many events exposed at least X {noun}?", "One line per country.")
        combined = pd.concat(summaries.values(), ignore_index=True)
        _chart(F.fig_exceedance(combined, measure, by="country", colors=F.country_colors(keys)), "compare-exceedance")
    rows = []
    for key, summary in summaries.items():
        values = pd.to_numeric(summary[f"{measure}_peak"], errors="coerce")
        rows.append({
            "Country": country_name(key), "Events": len(summary),
            f"Median peak {MEASURE_SHORT[measure].lower()}": values[values > 0].median(),
            f"Largest peak {MEASURE_SHORT[measure].lower()}": values.max(),
            "Warning-level events": int((summary["alert_level_max"] == "WARNING").sum()),
            "Also list other countries": int(summary["multi_country"].sum()),
            "Main hazards": counts.set_index("country_code")["top_hazards"].get(key, "–"),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={column: st.column_config.NumberColumn(format="compact") for column in rows[0] if column.startswith(("Median", "Largest"))})
    ui.note("Exposure values are never added across countries or events. An event shared by several countries appears in each.")


def _retrieval_table(results: dict[str, QueryResult]) -> pd.DataFrame:
    rows = []
    for key, result in results.items():
        for collection, api_result in (result.retrieval_state or {}).items():
            adaptations = sorted({item for partition in api_result.partitions for item in partition.adaptations if item != "reused_from_previous_attempt"})
            rows.append({
                "Country": country_name(key), "Collection": collection.replace("pdc-", ""),
                "Items": api_result.unique_count, "API pages": sum(len(partition.pages) for partition in api_result.partitions),
                "Complete": "Yes" if api_result.complete else "No", "Failed parts": len(api_result.failures),
                "Adaptations": ", ".join(adaptations) or "–",
            })
    return pd.DataFrame(rows)


def tab_quality(results: dict[str, QueryResult], views: dict[str, dict[str, Any]], key: str, summary: pd.DataFrame, measures: list[str]) -> None:
    view, result = views[key], results[key]
    left, right = st.columns(2, gap="large")
    with left:
        ui.section("How much PDC data exists per year?", f"{country_name(key)}: event updates and distinct events retrieved per year.")
        _chart(F.fig_coverage(view["coverage"]), "quality-coverage")
    with right:
        ui.section("Is each value present?", "Per event: published value, zero, not published, or conflicting.")
        labels = {"present_positive": "Value > 0", "present_zero": "Zero", "missing": "Not published", "unavailable": "No numeric value", "conflicting": "Conflicting", "not_retrieved": "Not retrieved"}
        # One row per measure so the table fits a half-width column.
        table = pd.DataFrame({MEASURE_SHORT[item]: summary[f"{item}_status"].map(labels).value_counts() for item in measures}).fillna(0).astype(int).T
        table = table[[label for label in labels.values() if label in table.columns]]
        st.dataframe(table, width="stretch")
    ui.section("Retrieval", "What was requested from the API and how it went. Adaptations are automatic retries with smaller requests after API errors.")
    st.dataframe(_retrieval_table(results), hide_index=True, width="stretch")
    for warning in result.metadata.warnings:
        ui.note(public_text(warning))
    caveats = summary[summary["caveat"].fillna("") != ""]
    if not caveats.empty:
        ui.section("Events with a caveat", "PDC title and hazard code disagree, or the event is a tsunami bulletin for a warning region.")
        st.dataframe(caveats[["title", "hazard_group", "caveat"]].rename(columns={"title": "Event", "hazard_group": "Hazard", "caveat": "Caveat"}), hide_index=True, width="stretch")
    if _has_age_bands(result.query):
        shares = age_shares(view["frames"])
        if not shares.empty:
            spread = shares.groupby("band")["share"].agg(lambda values: values.max() - values.min()).max()
            ui.note(f"Age bands: across {shares['family_key'].nunique()} events the share of any age band varies by at most {spread * 100:.1f} percentage points, consistent with PDC applying a national age structure.")
    with st.expander("Correlation evidence (how snapshots were linked to events)"):
        st.dataframe(public_frame(view["frames"].correlations).head(500), hide_index=True, width="stretch")


def tab_download(result: QueryResult, summary: pd.DataFrame, measure: str, selected: str | None) -> None:
    ui.section(
        "Event summary",
        "One row per event with peak and latest values for every measure. Contains the events currently shown: "
        "the 'Hazards shown' and 'Tsunami bulletins' settings apply.",
    )
    table = public_frame(summary.drop(columns=["footprint_url"], errors="ignore"))
    st.download_button("Download event summary (CSV)", table.to_csv(index=False).encode("utf-8"), file_name=f"pdc_event_summary_{result.query.country_code}.csv", mime="text/csv")
    ui.section("Full evidence bundle", "Tables, provenance and manifest for the selected country. Contains record IDs, never credentials.")
    # The bundle depends on the selected measure and event as well as the query,
    # so files prepared for an earlier selection are never offered.
    bundle_key = (result.query.fingerprint, MEASURE_CATEGORY[measure], selected)
    if st.button("Prepare evidence files"):
        with st.spinner("Building files…"):
            st.session_state["exports"] = (bundle_key, build_export_files(result, selected_category=MEASURE_CATEGORY[measure], selected_family_key=selected))
    stored = st.session_state.get("exports")
    if stored and stored[0] != bundle_key:
        ui.note("The measure or selected event changed since the files were prepared. Press Prepare evidence files again.")
    elif stored:
        columns = st.columns(3)
        for index, (name, data) in enumerate(stored[1].items()):
            columns[index % 3].download_button(name, data=data, file_name=name, key=f"download-{name}", width="stretch")


def tab_advanced(results: dict[str, QueryResult], views: dict[str, dict[str, Any]], key: str, selected: str | None, summary: pd.DataFrame) -> None:
    """Query JSON, request bodies, every retrieval window, and original STAC items (read-only)."""

    result = results[key]
    query = result.query
    left, right = st.columns(2, gap="large")
    with left:
        ui.section("Query", "The validated query and its fingerprint. The same query always retrieves the same evidence.")
        document = D.query_document(query)
        st.json(document, expanded=1)
        st.download_button("Download query (JSON)", D.json_text(document), file_name=f"pdc_query_{query.fingerprint[:12]}.json", mime="application/json", key=f"adv-query-{key}")
    with right:
        ui.section("Search request", "The POST /search body sent for the first month of each collection; other months differ only in their dates.")
        st.json(D.example_search_bodies(query), expanded=1)
        ui.note("Requests carry the bearer token in a header, which is never shown, stored or exported.")
    ui.section("Retrieval windows", "One row per month window (or smaller part, after an adaptation) with its pages. The API does not report a total, so every continuation link is followed to the end.")
    log = D.partition_log({key: result})
    st.dataframe(log, hide_index=True, width="stretch", height=320,
                 column_config={"complete": st.column_config.CheckboxColumn(), "from_cache": st.column_config.CheckboxColumn()})
    st.download_button("Download retrieval log (CSV)", log.to_csv(index=False).encode("utf-8"), file_name=f"pdc_retrieval_log_{query.fingerprint[:12]}.csv", mime="text/csv", key=f"adv-log-{key}")
    ui.section("Original PDC items", "The STAC items behind one event, exactly as the API returned them (long text is shortened for display).")
    if summary.empty:
        ui.note("No events in this view.")
        return
    keys = list(summary["family_key"])
    default = keys.index(selected) if selected in keys else 0
    titles = dict(zip(summary["family_key"], summary["title"], strict=False))
    family_key = st.selectbox("Event", keys, index=default, format_func=lambda item: F._short(titles.get(item) or item, 80), key=f"adv-event-{key}")
    ids = D.event_item_ids(views[key]["frames"], family_key)
    items = D.raw_items(result, ids)
    counts = ", ".join(f"{len(ids[name])} {name.replace('pdc-', '')}" for name in D.COLLECTIONS if ids.get(name))
    ui.note(f"Retained snapshots for this event: {counts or 'none'}. At most {D.RAW_ITEM_LIMIT} items per collection are shown.")
    available = [name for name in D.COLLECTIONS if items.get(name)]
    if not available:
        ui.note("The original items are not available for this event.")
        return
    collection = st.segmented_control("Collection", available, format_func=lambda name: name.replace("pdc-", "").title(), default=available[0], key=f"adv-collection-{key}") or available[0]
    chosen = items[collection]
    position = st.selectbox("Item", range(len(chosen)), format_func=lambda index: str(chosen[index].get("id")), key=f"adv-item-{key}-{collection}")
    st.json(chosen[position], expanded=2)


# ------------------------------------------------------------------ page

def render(state: dict[str, Any]) -> None:
    results: dict[str, QueryResult] = state["results"]
    views = state["views"]
    queries = state["queries"]
    keys = list(results)
    incomplete = [key for key in keys if not results[key].complete]
    names = ", ".join(country_name(key) for key in keys)
    if incomplete:
        ui.banner("warn", "Incomplete result", f"Some months could not be retrieved for {', '.join(country_name(key) for key in incomplete)}. Charts show what was retrieved; see Data quality.")
        if st.button("Retry failed parts", type="primary"):
            run_retrieval(queries, previous=results)
            st.rerun()
    else:
        pages = sum(len(partition.pages) for result in results.values() for api in (result.retrieval_state or {}).values() for partition in api.partitions)
        ui.banner("ok", "Complete", f"{names} · {queries[0].period_label} · retrieved {state['retrieved_at']} · {pages:,} API pages in {state['seconds']:.0f} s")

    with st.container(key="toolbar"):
        controls = st.columns((4, 5, 3) if len(keys) > 1 else (5, 3), gap="medium", vertical_alignment="bottom")
        if len(keys) > 1:
            with controls[0]:
                country = st.segmented_control("Country", keys, format_func=country_name, default=keys[0], key="view-country") or keys[0]
        else:
            country = keys[0]
        query = results[country].query
        measures = _measures_in(query)
        with controls[-2]:
            measure = st.segmented_control("Measure shown", measures, format_func=MEASURE_SHORT.get, default="people", key="view-measure") or "people"
        with controls[-1]:
            show_bulletins = st.toggle("Tsunami bulletins", value=False, help=BULLETIN_HELP)
        view = views[country]
        summary = view["summary"]
        if not show_bulletins:
            summary = summary[~_is_bulletin(summary)]
        present = [group for group in [*F.HAZARD_GROUP_ORDER, "Other"] if group in set(summary["hazard_group"])]
        if len(present) > 1:
            shown = st.pills("Hazards shown", present, selection_mode="multi", default=present, key=f"view-hazards-{country}")
            summary = summary[summary["hazard_group"].isin(shown or present)]
    summary = summary.reset_index(drop=True)

    noun = F.MEASURE_NOUNS[measure]
    values = pd.to_numeric(summary[f"{measure}_peak"], errors="coerce")
    positive = values[values > 0]
    largest = summary.loc[values.idxmax()] if values.notna().any() else None
    warnings = int((summary["alert_level_max"] == "WARNING").sum())
    snapshots = int(summary["snapshot_count"].sum())
    ui.kpis([
        ("Events", f"{len(summary):,}", f"{snapshots:,} PDC updates · {query.period_label}"),
        (f"Largest event · {noun}", compact(largest[f"{measure}_peak"]) if largest is not None else "–",
         f"{F._short(largest['title'], 42)} · {F._date(largest['event_date'])}" if largest is not None else "No values"),
        (f"Median per event · {noun}", compact(positive.median()) if not positive.empty else "–", f"{len(positive):,} of {len(summary):,} events with {noun} > 0"),
        ("Reached PDC 'Warning'", f"{warnings:,}", f"{warnings / len(summary):.0%} of events" if len(summary) else "–"),
    ])

    if summary.empty:
        ui.empty_state("No events in this view", "Change the hazards shown or the retrieval settings.")
        return
    selected = st.session_state.get("selected_event")
    if selected not in set(summary["family_key"]):
        selected = str(summary.loc[values.idxmax(), "family_key"]) if values.notna().any() else str(summary["family_key"].iloc[0])
        st.session_state["selected_event"] = selected

    names_tabs = ["Overview", "Map", "Events", "Exposure", "Event detail"] + (["Compare countries"] if len(keys) > 1 else []) + ["Data quality", "Advanced", "Download"]
    tabs = dict(zip(names_tabs, st.tabs(names_tabs), strict=True))
    with tabs["Overview"]:
        tab_overview(summary, query, measure)
    with tabs["Map"]:
        tab_map(summary, measure, show_bulletins, selected, measures, country)
    with tabs["Events"]:
        tab_events(summary, measure)
    with tabs["Exposure"]:
        tab_exposure(summary, query, measure, measures)
    with tabs["Event detail"]:
        tab_event_detail(summary, view["frames"], query, measure, measures)
    if "Compare countries" in tabs:
        with tabs["Compare countries"]:
            tab_compare(views, keys, query, measure, show_bulletins)
    with tabs["Data quality"]:
        tab_quality(results, views, country, summary, measures)
    with tabs["Advanced"]:
        tab_advanced(results, views, country, st.session_state.get("selected_event"), summary)
    with tabs["Download"]:
        tab_download(results[country], summary, measure, st.session_state.get("selected_event"))


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🌍", layout="wide", initial_sidebar_state="expanded")
    ui.inject_css()
    state = st.session_state.get("pdc")
    loaded = [f"{', '.join(country_name(query.country_code) for query in state['queries'])} · {state['queries'][0].period_label}"] if state else []
    ui.page_header(
        APP_TITLE,
        "Where and when disasters happened, and how many people, homes, schools and hospitals PDC estimated to be exposed. "
        "Values are PDC exposure estimates, not confirmed impacts.",
        ["Live Montandon API", "Read-only", *loaded],
    )
    queries, clicked = sidebar()
    # Fixed slot for the retrieval status: without it the tabs below shift
    # position on the next rerun, and Streamlit resets them to the first tab.
    status_slot = st.container()
    if clicked and queries:
        with status_slot:
            run_retrieval(queries)
    state = st.session_state.get("pdc")
    if not state:
        ui.empty_state("Choose countries and years, then press Retrieve data", "The dashboard loads PDC events, hazard alerts and exposure values for up to 5 countries and 5 years.")
        st.info("No retrieval occurs on page load. Data is requested from the live Montandon API only when you press Retrieve data.")
        return
    render(state)


if __name__ == "__main__":
    main()
