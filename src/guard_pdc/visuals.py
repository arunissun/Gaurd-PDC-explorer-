"""Stage 6 figures and Stage 7 map layers from shared analytical frames."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import http.client
import json
import math
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .api import open_without_redirects
from .analysis import AnalysisFrames, TEMPORAL_NOTE, temporal_history, temporal_summary
from .models import EventSnapshotRow
from .taxonomy import AGE_BAND_CATEGORIES, CATEGORY_ORDER, category_label


MAP_NOTE = "PDC point represents the event/hazard location; it is not automatically an impact footprint."
EXPOSURE_NOTE = "Values are original PDC exposure estimates, not confirmed humanitarian outcomes."
HAZARD_PALETTE = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#F0E442")
STATUS_COLORS = {
    "present_positive": "#0072B2",
    "present_zero": "#56B4E9",
    "conflicting": "#D55E00",
    "missing": "#999999",
    "unavailable": "#CC79A7",
    "not_retrieved": "#DDDDDD",
}
STATUS_LABELS = {
    "present_positive": "Present positive",
    "present_zero": "Present zero",
    "conflicting": "Conflicting",
    "missing": "Missing",
    "unavailable": "Unavailable value",
    "not_retrieved": "Not retrieved",
}


@dataclass(frozen=True, slots=True)
class MapLayers:
    event_points: pd.DataFrame
    hazard_points: pd.DataFrame
    impact_points: pd.DataFrame
    country_counts: pd.DataFrame
    render_mode: str
    selected_category: str
    warnings: tuple[str, ...] = ()
    disclaimer: str = MAP_NOTE


@dataclass(frozen=True, slots=True)
class FootprintResult:
    status: str
    message: str
    geojson: Mapping[str, Any] | None = None
    asset_key: str | None = None
    host: str | None = None
    feature_count: int = 0


def _joined(values: Any) -> str:
    return ", ".join(str(value) for value in values if value is not None)


def hazard_color(label: str) -> str:
    index = int.from_bytes(sha256(label.encode("utf-8")).digest()[:2], "big") % len(HAZARD_PALETTE)
    return HAZARD_PALETTE[index]


def _rgb(value: str, alpha: int = 210) -> list[int]:
    return [int(value[index : index + 2], 16) for index in (1, 3, 5)] + [alpha]


def _period(frames: AnalysisFrames) -> str:
    months = ",".join(str(month) for month in frames.query.months)
    return f"{frames.query.year}; month(s) {months}"


def _finish(
    fig: go.Figure,
    frames: AnalysisFrames,
    title: str,
    grain: str,
    *,
    exposure: bool = False,
    extra_note: str | None = None,
) -> go.Figure:
    note = f"Grain: {grain} | Period: {_period(frames)} | Provider: {frames.metadata.provider}"
    subtitle = f"<sup>{note}</sup>"
    if exposure:
        subtitle += f"<br><sup>{EXPOSURE_NOTE}</sup>"
    if extra_note:
        subtitle += f"<br><sup>{extra_note}</sup>"
    fig.update_layout(
        template="plotly_white",
        title={"text": f"{title}<br>{subtitle}", "x": 0.01},
        margin={"l": 70, "r": 30, "t": 125 if extra_note else 105 if exposure else 90, "b": 60},
        legend_title_text="",
        meta={"grain": grain, "period": _period(frames), "provider": frames.metadata.provider, "exposure_note": EXPOSURE_NOTE if exposure else None, "note": extra_note},
    )
    return fig


def _empty(frames: AnalysisFrames, title: str, grain: str, message: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper")
    return _finish(fig, frames, title, grain)


def evidence_cards(frames: AnalysisFrames) -> dict[str, int]:
    provenance = frames.provenance
    grouped = (
        provenance.groupby(["collection", "item_id"])["provider"].agg(lambda values: frozenset(values))
        if not provenance.empty
        else pd.Series(dtype=object)
    )
    return {
        "event_families": int(len(frames.event_families)),
        "event_snapshots": int(len(frames.event_snapshots)),
        "hazard_records": int(frames.hazards["hazard_item_id"].nunique()) if not frames.hazards.empty else 0,
        "impact_observations": int(len(frames.impacts)),
        "selected_categories": int(len(frames.query.categories)),
        "api_only_items": int(sum(value == {"api"} for value in grouped)),
        "local_only_items": int(sum(value == {"local"} for value in grouped)),
        "overlap_items": int(sum(value == {"api", "local"} for value in grouped)),
        "payload_conflicts": int(frames.quality.set_index("metric")["value"].get("payload_conflicts", 0)) if not frames.quality.empty else 0,
    }


def figure_monthly_events(frames: AnalysisFrames) -> go.Figure:
    if frames.event_families.empty:
        return _empty(frames, "Monthly PDC source events by hazard", "one source event family", "No event families in this result.")
    data = frames.event_families.copy()
    dates = pd.to_datetime(data["start_datetime"], errors="coerce", utc=True)
    data["month"] = dates.dt.month
    data["hazard"] = data["hazard_labels"].map(lambda values: sorted(values)[0] if values else "Unspecified")
    data = data.dropna(subset=["month"]).drop_duplicates(["family_key"])
    counts = data.groupby(["month", "hazard"])["family_key"].nunique().reset_index(name="event_families")
    fig = go.Figure()
    for hazard in sorted(counts["hazard"].unique()):
        selected = counts[counts["hazard"] == hazard]
        fig.add_bar(x=selected["month"], y=selected["event_families"], name=hazard, marker_color=hazard_color(hazard))
    fig.update_layout(barmode="stack")
    fig.update_xaxes(title="Month", dtick=1)
    fig.update_yaxes(title="Distinct source event families", rangemode="tozero")
    return _finish(fig, frames, "Monthly PDC source events by hazard", "one source event family")


def figure_snapshot_counts(frames: AnalysisFrames, *, limit: int = 100) -> go.Figure:
    if frames.event_families.empty:
        return _empty(frames, "Snapshot count by source event", "one source event family", "No event families in this result.")
    data = frames.event_families.nlargest(limit, "snapshot_count").sort_values("snapshot_count")
    labels = data["event_title"].fillna("Untitled") + " — " + data["family_key"]
    fig = go.Figure(go.Bar(x=data["snapshot_count"], y=labels, orientation="h", marker_color="#0072B2"))
    fig.update_xaxes(title="Exact event items")
    return _finish(fig, frames, "Snapshot count by source event", "one source event family")


def _category_availability_data(frames: AnalysisFrames, hazard: str | None = None) -> pd.DataFrame:
    data = frames.category_status.copy()
    if data.empty:
        return data
    if hazard:
        data = data[data["hazard_labels"].map(lambda values: hazard in values)]
    denominator = data["family_key"].nunique()
    if not denominator:
        return pd.DataFrame()
    counts = data.groupby(["impact_category", "category_label", "status"])["family_key"].nunique().reset_index(name="events")
    counts["percent"] = counts["events"] * 100 / denominator
    counts["category_order"] = counts["impact_category"].map({value: index for index, value in enumerate(CATEGORY_ORDER)}).fillna(999)
    return counts.sort_values(["category_order", "status"], kind="stable")


def figure_category_availability(frames: AnalysisFrames, hazard: str | None = None) -> go.Figure:
    data = _category_availability_data(frames, hazard)
    if data.empty:
        return _empty(frames, "Category availability by hazard", "event-family/category cell", "No category evidence for the selected hazard.")
    fig = go.Figure()
    for status in STATUS_COLORS:
        selected = data[data["status"] == status]
        fig.add_bar(
            x=selected["percent"],
            y=selected["category_label"],
            name=STATUS_LABELS[status],
            orientation="h",
            marker_color=STATUS_COLORS[status],
            customdata=selected["events"],
            hovertemplate="%{y}: %{x:.1f}% (%{customdata} events)<extra></extra>",
        )
    fig.update_layout(barmode="stack")
    fig.update_xaxes(title="Percent of distinct event families", range=[0, 100])
    return _finish(fig, frames, "Category availability by hazard", "event-family/category cell", exposure=True)


def _completeness_data(frames: AnalysisFrames) -> pd.DataFrame:
    data = frames.category_status[frames.category_status["status"] != "not_retrieved"].copy()
    if data.empty:
        return data
    # One status row exists per valid impact type/category pair, so the row
    # count is the denominator. (Distinct category names undercounted it when
    # two impact types were selected, which produced 200% bars.)
    totals = data.groupby("family_key").size().rename("expected")
    counts = data.groupby(["family_key", "event_title", "status"], dropna=False).size().rename("count").reset_index()
    counts = counts.join(totals, on="family_key")
    counts["percent"] = counts["count"] * 100 / counts["expected"]
    return counts


def figure_event_completeness(frames: AnalysisFrames, *, limit: int = 100) -> go.Figure:
    data = _completeness_data(frames)
    if data.empty:
        return _empty(frames, "Event evidence completeness", "event-family/requested-category cell", "No retrieved impact categories to assess.")
    ranking = (
        data[data["status"].isin({"present_positive", "present_zero"})]
        .groupby("family_key")["percent"]
        .sum()
        .sort_values(ascending=False)
        .head(limit)
    )
    data = data[data["family_key"].isin(ranking.index)].copy()
    titles = data.groupby("family_key")["event_title"].first().fillna("Untitled")
    labels = {key: f"{titles[key]} — {key}" for key in ranking.index}
    fig = go.Figure()
    for status in STATUS_COLORS:
        selected = data[data["status"] == status].set_index("family_key").reindex(ranking.index).fillna({"percent": 0})
        fig.add_bar(
            x=selected["percent"],
            y=[labels[key] for key in ranking.index],
            name=STATUS_LABELS[status],
            orientation="h",
            marker_color=STATUS_COLORS[status],
        )
    fig.update_layout(barmode="stack")
    fig.update_xaxes(title="Percent of requested categories", range=[0, 100])
    return _finish(fig, frames, "Event evidence completeness", "one source event family")


def figure_selected_category(frames: AnalysisFrames, category: str, *, log_scale: bool = False) -> go.Figure:
    data = frames.category_status[frames.category_status["impact_category"] == category].copy()
    if data.empty:
        return _empty(frames, f"{category_label(category)} across events", "one selected observation per event family", "The category was not available in the analytical frame.")
    data["display_value"] = pd.to_numeric(data["numeric_value"], errors="coerce")
    data.loc[data["status"].isin({"missing", "unavailable", "conflicting", "not_retrieved"}), "display_value"] = 0
    labels = data["event_title"].fillna("Untitled") + " — " + data["family_key"]
    fig = go.Figure()
    symbols = {"present_positive": "circle", "present_zero": "circle-open", "conflicting": "diamond-open", "missing": "x", "unavailable": "square-open", "not_retrieved": "line-ew"}
    for status in STATUS_COLORS:
        selected = data[data["status"] == status]
        fig.add_scatter(
            x=selected["display_value"],
            y=labels[selected.index],
            mode="markers",
            name=STATUS_LABELS[status],
            marker={"color": STATUS_COLORS[status], "symbol": symbols[status], "size": 10},
            customdata=selected[["status", "original_unit"]].astype(str),
            hovertemplate="%{y}<br>value=%{x}<br>status=%{customdata[0]}<br>unit=%{customdata[1]}<extra></extra>",
        )
    if log_scale:
        fig.update_xaxes(type="log")
    fig.update_xaxes(title=f"Original {category_label(category)} value (non-value states plotted at zero)")
    return _finish(fig, frames, f"{category_label(category)} across events", "one selected observation per event family", exposure=True)


def figure_age_profile(frames: AnalysisFrames, family_key: str) -> go.Figure:
    data = frames.demographics[
        (frames.demographics["family_key"] == family_key)
        & (frames.demographics["impact_category"].isin(AGE_BAND_CATEGORIES))
    ].sort_values("age_order")
    if data.empty:
        return _empty(frames, "Selected-event age profile", "one selected age-band observation", "No age-band evidence was retrieved for this event.")
    fig = go.Figure(go.Bar(x=data["numeric_value"], y=data["category_label"], orientation="h", marker_color="#009E73"))
    people = frames.demographics[
        (frames.demographics["family_key"] == family_key) & (frames.demographics["impact_category"] == "people")
    ]
    people_text = "unavailable" if people.empty or pd.isna(people.iloc[0]["numeric_value"]) else f"{people.iloc[0]['numeric_value']:,.0f}"
    fig.add_annotation(text=f"Separate people value: {people_text}", x=1, y=1.08, xref="paper", yref="paper", showarrow=False, xanchor="right")
    fig.update_xaxes(title="Original PDC exposure value")
    return _finish(fig, frames, "Selected-event age profile", "one selected observation per age band", exposure=True)


def figure_age_reconciliation(frames: AnalysisFrames, family_key: str) -> go.Figure:
    data = frames.demographics[frames.demographics["family_key"] == family_key]
    ages = pd.to_numeric(data[data["impact_category"].isin(AGE_BAND_CATEGORIES)]["numeric_value"], errors="coerce")
    people = pd.to_numeric(data[data["impact_category"] == "people"]["numeric_value"], errors="coerce")
    age_sum = ages.sum(min_count=len(AGE_BAND_CATEGORIES))
    people_value = people.iloc[0] if not people.empty else math.nan
    fig = go.Figure(go.Bar(x=["Available age-band sum", "Separate people value"], y=[age_sum, people_value], marker_color=["#009E73", "#0072B2"]))
    if pd.notna(age_sum) and pd.notna(people_value):
        difference = age_sum - people_value
        percent = difference * 100 / people_value if people_value else math.nan
        fig.add_annotation(text=f"Difference: {difference:,.0f} ({percent:.1f}%)" if pd.notna(percent) else f"Difference: {difference:,.0f}", x=0.5, y=1.08, xref="paper", yref="paper", showarrow=False)
    else:
        fig.add_annotation(text="Reconciliation unavailable: not all age bands and people were retrieved.", x=0.5, y=1.08, xref="paper", yref="paper", showarrow=False)
    fig.update_yaxes(title="Original PDC exposure value")
    return _finish(fig, frames, "Age-band reconciliation", "one selected observation per category", exposure=True)


def figure_exposure_panels(frames: AnalysisFrames, family_key: str) -> dict[str, go.Figure]:
    panels = {}
    for category in ("households", "schools", "hospitals", "global_currency"):
        data = frames.category_status[
            (frames.category_status["family_key"] == family_key)
            & (frames.category_status["impact_category"] == category)
        ]
        fig = go.Figure()
        if not data.empty and data.iloc[0]["status"] in {"present_positive", "present_zero"}:
            row = data.iloc[0]
            fig.add_trace(go.Indicator(mode="number", value=float(row["numeric_value"]), number={"suffix": f" {row['original_unit']}" if row["original_unit"] is not None else ""}))
        else:
            state = data.iloc[0]["status"] if not data.empty else "missing"
            fig.add_annotation(text=STATUS_LABELS.get(state, state), showarrow=False, x=0.5, y=0.5)
        panels[category] = _finish(fig, frames, category_label(category), "one selected observation", exposure=True)
    return panels


def figure_distribution(frames: AnalysisFrames, category: str) -> go.Figure:
    data = frames.category_status[
        (frames.category_status["impact_category"] == category)
        & (frames.category_status["status"].isin({"present_positive", "present_zero"}))
    ].copy()
    values = pd.to_numeric(data["numeric_value"], errors="coerce").dropna().sort_values()
    if values.empty:
        return _empty(frames, f"Distribution of {category_label(category)}", "one selected observation per event family", "No numeric values are available.")
    fig = make_subplots(rows=1, cols=2, subplot_titles=("Histogram", "ECDF"))
    fig.add_trace(go.Histogram(x=values, marker_color="#0072B2", name="Events"), row=1, col=1)
    fig.add_trace(go.Scatter(x=values, y=[(index + 1) / len(values) for index in range(len(values))], mode="lines+markers", marker_color="#009E73", name="ECDF"), row=1, col=2)
    fig.update_xaxes(title=category_label(category))
    fig.update_yaxes(title="Event families", row=1, col=1)
    fig.update_yaxes(title="Cumulative proportion", range=[0, 1], row=1, col=2)
    return _finish(fig, frames, f"Distribution of {category_label(category)}", "one selected observation per event family", exposure=True)


def figure_event_duration(frames: AnalysisFrames, *, limit: int = 100) -> go.Figure:
    if frames.event_families.empty:
        return _empty(frames, "Event duration and observation window", "one source event family", "No event dates are available.")
    data = frames.event_families.head(limit).copy()
    data["start"] = pd.to_datetime(data["start_datetime"], errors="coerce", utc=True)
    data["end"] = pd.to_datetime(data["end_datetime"], errors="coerce", utc=True)
    data = data.dropna(subset=["start", "end"])
    fig = go.Figure()
    for row in data.itertuples():
        label = f"{row.event_title or 'Untitled'} — {row.family_key}"
        fig.add_scatter(x=[row.start, row.end], y=[label, label], mode="lines+markers", line={"color": "#0072B2", "width": 4}, marker={"size": 7}, showlegend=False, hovertemplate="%{y}<br>%{x}<extra></extra>")
    return _finish(fig, frames, "Event duration and observation window", "one source event family")


def figure_snapshot_timeline(frames: AnalysisFrames, family_key: str) -> go.Figure:
    data = frames.event_snapshots[frames.event_snapshots["family_key"] == family_key].copy()
    if data.empty:
        return _empty(frames, "PDC exposure snapshot timeline", "one retained event snapshot", "No event snapshots were retrieved for this event family.")
    data["exposure_snapshot_time"] = pd.to_datetime(data["snapshot_time"], errors="coerce", utc=True)
    data = data.dropna(subset=["exposure_snapshot_time"]).sort_values(["exposure_snapshot_time", "event_item_id"], kind="stable")
    if data.empty:
        fig = go.Figure()
        fig.add_annotation(text="No verified PDC exposure snapshot times are available for this event family.", showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper")
        return _finish(fig, frames, "PDC exposure snapshot timeline", "one retained event snapshot", extra_note=TEMPORAL_NOTE)
    fig = go.Figure()
    fig.add_scatter(
        x=data["exposure_snapshot_time"],
        y=[1] * len(data),
        mode="markers",
        name="Exposure snapshot",
        marker={"color": "#0072B2", "size": 10, "symbol": "circle"},
        customdata=data[["event_item_id", "snapshot_time_method", "provider", "processing_version"]].fillna("Unavailable"),
        hovertemplate="Exposure snapshot: %{x}<br>item=%{customdata[0]}<br>time method=%{customdata[1]}<br>provider=%{customdata[2]}<br>processing version=%{customdata[3]}<extra></extra>",
    )
    event_datetimes = pd.to_datetime(data["event_datetime"], errors="coerce", utc=True).dropna().drop_duplicates()
    if not event_datetimes.empty:
        fig.add_scatter(
            x=event_datetimes,
            y=[0] * len(event_datetimes),
            mode="markers",
            name="Event datetime",
            marker={"color": "#222222", "size": 10, "symbol": "diamond"},
            hovertemplate="Event datetime: %{x}<extra></extra>",
        )
    family = frames.event_families[frames.event_families["family_key"] == family_key]
    if not family.empty:
        bounds = (
            ("Event start", family.iloc[0]["start_datetime"], "triangle-right"),
            ("Event end", family.iloc[0]["end_datetime"], "square"),
        )
        for label, value, symbol in bounds:
            timestamp = pd.to_datetime(value, errors="coerce", utc=True)
            if pd.notna(timestamp):
                fig.add_scatter(x=[timestamp], y=[0], mode="markers", name=label, marker={"color": "#666666", "size": 10, "symbol": symbol}, hovertemplate=f"{label}: %{{x}}<extra></extra>")
    fig.update_yaxes(tickvals=[0, 1], ticktext=["Event dates", "Exposure snapshots"], range=[-0.4, 1.4])
    fig.update_xaxes(title="Exposure snapshot time (UTC)")
    return _finish(fig, frames, "PDC exposure snapshot timeline", "one retained event snapshot", extra_note=TEMPORAL_NOTE)


def figure_change_timeline(frames: AnalysisFrames, family_key: str, category: str) -> go.Figure:
    title = f"PDC exposure snapshot value — {category_label(category)}"
    history = temporal_history(frames, family_key, category)
    summary = temporal_summary(frames, family_key, category)
    valid = history[history["valid_for_change"]].copy()
    if summary.status == "insufficient":
        fig = go.Figure()
        fig.add_annotation(text=summary.message, showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper")
        return _finish(fig, frames, title, "one retained exposure snapshot", exposure=True, extra_note=TEMPORAL_NOTE)
    plotted = valid[valid["is_change_point"]].copy()
    endpoints = valid.sort_values(["series_key", "exposure_snapshot_time", "impact_item_id"], kind="stable").groupby("series_key", as_index=False).tail(1)
    plotted = pd.concat([plotted, endpoints]).drop_duplicates(["series_key", "exposure_snapshot_time", "numeric_value"]).sort_values(["series_key", "exposure_snapshot_time"], kind="stable")
    fig = go.Figure()
    for series, data in plotted.groupby("series_key", sort=True):
        unit = data.iloc[0]["original_unit"]
        label = f"{data.iloc[0]['provider']} | {data.iloc[0]['country_code'] or 'all'} | unit {unit if unit is not None else 'unspecified'}"
        fig.add_scatter(
            x=data["exposure_snapshot_time"],
            y=data["numeric_value"],
            mode="lines+markers",
            line={"color": "#0072B2", "shape": "hv", "width": 2},
            marker={"size": 8, "symbol": "circle"},
            name=label,
            customdata=data[["impact_item_id", "direction", "snapshot_time_method", "provider"]].fillna("Unavailable"),
            hovertemplate="Exposure snapshot: %{x}<br>value=%{y}<br>item=%{customdata[0]}<br>change=%{customdata[1]}<br>time method=%{customdata[2]}<br>provider=%{customdata[3]}<extra></extra>",
        )
    fig.update_xaxes(title="Exposure snapshot time (UTC)")
    fig.update_yaxes(title="Original PDC exposure value")
    fig = _finish(fig, frames, title, "one retained exposure snapshot", exposure=True, extra_note=TEMPORAL_NOTE)
    fig.update_layout(meta={**dict(fig.layout.meta), "temporal_status": summary.status, "valid_snapshots": summary.valid_snapshots, "change_points": summary.change_points})
    return fig


def figure_geometry_quality(frames: AnalysisFrames) -> go.Figure:
    if frames.geometry.empty:
        return _empty(frames, "Geometry quality", "one distinct evidence item", "No geometry evidence is available.")
    data = frames.geometry.copy()
    measures = {
        "Valid": int(data["valid_geometry"].sum()),
        "Missing": int(data["missing_geometry"].sum()),
        "Invalid/non-point": int((~data["valid_geometry"] & ~data["missing_geometry"]).sum()),
        "Cloned across collections": int(data.get("cloned_coordinate", pd.Series(dtype=bool)).sum()),
    }
    fig = go.Figure(go.Bar(x=list(measures), y=list(measures.values()), marker_color=["#009E73", "#999999", "#D55E00", "#E69F00"]))
    fig.update_yaxes(title="Distinct evidence items", rangemode="tozero")
    return _finish(fig, frames, "Geometry quality", "one distinct evidence item")


def static_fallbacks(frames: AnalysisFrames) -> dict[str, pd.DataFrame]:
    """Readable tables for environments where interactive Plotly is unavailable."""

    monthly = frames.event_families.copy()
    if not monthly.empty:
        monthly["month"] = pd.to_datetime(monthly["start_datetime"], errors="coerce", utc=True).dt.month
        monthly = monthly.groupby("month")["family_key"].nunique().reset_index(name="event_families")
    return {
        "monthly_events": monthly,
        "snapshot_counts": frames.event_families[["family_key", "event_title", "snapshot_count"]].copy() if not frames.event_families.empty else pd.DataFrame(),
        "category_availability": _category_availability_data(frames),
        "event_completeness": _completeness_data(frames),
        "geometry_quality": frames.geometry.copy(),
    }


def _latest_event_rows(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events
    data = events.assign(_time=events["snapshot_time"].fillna(events["event_datetime"]).fillna(""))
    return data.sort_values(["family_key", "_time", "event_item_id"], kind="stable").groupby("family_key", as_index=False).tail(1)


def build_map_layers(frames: AnalysisFrames, *, selected_category: str = "people", selected_family_key: str | None = None) -> MapLayers:
    latest = _latest_event_rows(frames.event_snapshots)
    values = frames.category_status[frames.category_status["impact_category"] == selected_category]
    values = values.drop_duplicates("family_key", keep="last") if not values.empty else values
    if not latest.empty:
        latest = latest.merge(
            frames.event_families[["family_key", "snapshot_count", "country_codes", "hazard_labels"]],
            on="family_key",
            how="left",
            suffixes=("", "_family"),
        )
        if not values.empty:
            latest = latest.merge(values[["family_key", "numeric_value", "original_unit", "status", "selected_time"]], on="family_key", how="left")
        else:
            latest = latest.assign(numeric_value=None, original_unit=None, status="not_retrieved", selected_time=None)
        latest = latest[latest["valid_geometry"]].drop_duplicates("family_key")
        latest["hazard_label"] = latest["hazard_labels"].map(lambda item: sorted(item)[0] if isinstance(item, tuple) and item else "Unspecified")
        latest["color"] = latest["hazard_label"].map(hazard_color)
        latest["color_rgb"] = latest["color"].map(_rgb)
        numeric = pd.to_numeric(latest["numeric_value"], errors="coerce").clip(lower=0)
        latest["point_size"] = numeric.map(lambda value: min(30.0, max(6.0, math.sqrt(value) / 10)) if pd.notna(value) else 6.0)
        latest["tooltip"] = latest.apply(
            lambda row: (
                f"{row['event_title'] or 'Untitled'} | {row['family_key']} | {row['hazard_label']} | "
                f"countries: {_joined(row['country_codes_family'])} | snapshots: {row['snapshot_count']} | "
                f"{category_label(selected_category)}: {row['numeric_value']} {row['original_unit'] or ''} | "
                f"provider: {row['provider']} | {MAP_NOTE}"
            ),
            axis=1,
        )
    event_points = latest.reset_index(drop=True)

    hazards = frames.hazards.copy()
    impacts = frames.impacts.copy()
    if selected_family_key:
        hazards = hazards[hazards["family_key"] == selected_family_key] if not hazards.empty else hazards
        impacts = impacts[impacts["family_key"] == selected_family_key] if not impacts.empty else impacts
    if not hazards.empty:
        hazards = hazards[hazards["valid_geometry"]].copy()
        hazards = (
            hazards.groupby(["family_key", "longitude", "latitude", "provider"], as_index=False)
            .agg(
                hazard_item_ids=("hazard_item_id", lambda values: tuple(dict.fromkeys(values))),
                hazard_labels=("hazard_labels", lambda values: tuple(dict.fromkeys(label for group in values for label in group))),
            )
        )
        hazards["tooltip"] = hazards.apply(lambda row: f"Hazard {_joined(row['hazard_item_ids'])} | {_joined(row['hazard_labels'])} | provider: {row['provider']} | {MAP_NOTE}", axis=1)
    if not impacts.empty:
        impacts = impacts[impacts["valid_geometry"]].copy()
        impacts = (
            impacts.groupby(["family_key", "longitude", "latitude", "provider"], as_index=False)
            .agg(
                impact_item_ids=("impact_item_id", lambda values: tuple(dict.fromkeys(values))),
                categories=("category_label", lambda values: tuple(dict.fromkeys(values))),
            )
        )
        impacts["tooltip"] = impacts.apply(lambda row: f"Impact {_joined(row['impact_item_ids'])} | {_joined(row['categories'])} | provider: {row['provider']} | {MAP_NOTE}", axis=1)

    country_records = []
    if not frames.event_families.empty:
        exploded = frames.event_families[["family_key", "country_codes", "snapshot_count", "hazard_labels"]].explode("country_codes")
        for country, data in exploded.dropna(subset=["country_codes"]).groupby("country_codes"):
            country_records.append(
                {
                    "country_code": country,
                    "event_families": int(data["family_key"].nunique()),
                    "event_snapshots": int(data.drop_duplicates("family_key")["snapshot_count"].sum()),
                    "multi_country_events": int(sum(len(value) > 1 for value in frames.event_families[frames.event_families["family_key"].isin(data["family_key"])]["country_codes"])),
                    "hazards": _joined(sorted({hazard for values in data["hazard_labels"] for hazard in values})),
                }
            )
    warnings = []
    missing = len(frames.event_families) - len(event_points)
    if missing:
        warnings.append(f"{missing} event families have no valid point and remain in tables only")
    return MapLayers(
        event_points=event_points,
        hazard_points=hazards.reset_index(drop=True),
        impact_points=impacts.reset_index(drop=True),
        country_counts=pd.DataFrame.from_records(country_records),
        render_mode="individual" if len(event_points) <= 5_000 else "hexagon",
        selected_category=selected_category,
        warnings=tuple(warnings),
    )


def country_choropleth_geojson(
    layers: MapLayers,
    boundaries: Mapping[str, Any],
    *,
    iso3_property: str = "ISO_A3",
) -> Mapping[str, Any]:
    """Join distinct event-family counts to supplied country boundaries."""

    features = boundaries.get("features") if boundaries.get("type") == "FeatureCollection" else None
    if not isinstance(features, list):
        raise ValueError("country boundaries must be a GeoJSON FeatureCollection")
    counts = layers.country_counts.set_index("country_code").to_dict("index") if not layers.country_counts.empty else {}
    ranked = sorted({int(value["event_families"]) for value in counts.values()})
    colors = ("#F7FBFF", "#C6DBEF", "#6BAED6", "#2171B5", "#08306B")
    output = []
    for feature in features:
        if not isinstance(feature, Mapping) or not isinstance(feature.get("properties"), Mapping):
            raise ValueError("country boundary features require object properties")
        geometry = feature.get("geometry")
        if not isinstance(geometry, Mapping) or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            raise ValueError("country boundary features must use Polygon or MultiPolygon geometry")
        properties = dict(feature["properties"])
        code = str(properties.get(iso3_property) or "")
        values = counts.get(code, {"event_families": 0, "event_snapshots": 0, "multi_country_events": 0, "hazards": ""})
        count = int(values["event_families"])
        rank = ranked.index(count) / max(1, len(ranked) - 1) if count in ranked else 0
        color = colors[min(len(colors) - 1, int(rank * (len(colors) - 1)))]
        properties.update(
            {
                "pdc_country_code": code,
                "pdc_event_families": count,
                "pdc_event_snapshots": int(values["event_snapshots"]),
                "pdc_multi_country_events": int(values["multi_country_events"]),
                "pdc_hazards": values["hazards"],
                "pdc_fill_color": _rgb(color, 180),
                "pdc_count_note": "Country totals are not mutually exclusive and must not be summed globally.",
            }
        )
        output.append({"type": "Feature", "geometry": geometry, "properties": properties})
    return {"type": "FeatureCollection", "features": output}


def to_pydeck(layers: MapLayers, *, country_boundaries: Mapping[str, Any] | None = None):
    """Create the dashboard map from the same deduplicated layer data."""

    import pydeck as pdk

    deck_layers = []
    if country_boundaries is not None:
        choropleth = country_choropleth_geojson(layers, country_boundaries)
        deck_layers.append(
            pdk.Layer(
                "GeoJsonLayer",
                choropleth,
                get_fill_color="properties.pdc_fill_color",
                get_line_color=[80, 80, 80, 180],
                line_width_min_pixels=0.5,
                pickable=True,
                auto_highlight=True,
            )
        )
    event_data = layers.event_points.to_dict("records")
    if event_data:
        if layers.render_mode == "hexagon":
            deck_layers.append(pdk.Layer("HexagonLayer", event_data, get_position="[longitude, latitude]", radius=25_000, elevation_scale=0, pickable=True))
        else:
            deck_layers.append(
                pdk.Layer(
                    "ScatterplotLayer",
                    event_data,
                    get_position="[longitude, latitude]",
                    get_radius="point_size",
                    radius_units="pixels",
                    get_fill_color="color_rgb",
                    get_line_color=[30, 30, 30, 255],
                    line_width_min_pixels=1,
                    stroked=True,
                    pickable=True,
                )
            )
    for frame, color in ((layers.hazard_points, [213, 94, 0, 180]), (layers.impact_points, [0, 158, 115, 160])):
        if not frame.empty:
            deck_layers.append(pdk.Layer("ScatterplotLayer", frame.to_dict("records"), get_position="[longitude, latitude]", get_radius=8, radius_units="pixels", get_fill_color=color, get_line_color=[20, 20, 20, 255], stroked=True, pickable=True))
    longitude = float(layers.event_points["longitude"].mean()) if not layers.event_points.empty else 0.0
    latitude = float(layers.event_points["latitude"].mean()) if not layers.event_points.empty else 0.0
    return pdk.Deck(
        layers=deck_layers,
        initial_view_state=pdk.ViewState(longitude=longitude, latitude=latitude, zoom=3 if len(event_data) > 1 else 8),
        map_style="light",
        tooltip={"text": "{tooltip}"},
        description=layers.disclaimer,
    )


def _arrow_points(frame: pd.DataFrame):
    import numpy as np
    import pyarrow as pa

    coordinates = pa.array(frame[["longitude", "latitude"]].values.tolist(), type=pa.list_(pa.float64(), 2))
    geometry_field = pa.field(
        "geometry",
        coordinates.type,
        metadata={
            b"ARROW:extension:name": b"geoarrow.point",
            b"ARROW:extension:metadata": json.dumps({"crs": "EPSG:4326", "crs_type": "authority_code"}).encode("utf-8"),
        },
    )
    tooltips = pa.array(frame["tooltip"].fillna("").astype(str))
    table = pa.Table.from_arrays([coordinates, tooltips], schema=pa.schema([geometry_field, pa.field("tooltip", pa.string())]))
    radii = np.asarray(frame.get("point_size", pd.Series(8, index=frame.index)), dtype="float64")
    colors = np.asarray([_rgb(value) for value in frame.get("color", pd.Series("#0072B2", index=frame.index))], dtype="uint8")
    return table, radii, colors


def to_lonboard(layers: MapLayers):
    """Create a notebook map and Manywidgets controls from shared map data."""

    from lonboard import HeatmapLayer, Map, ScatterplotLayer
    from lonboard.basemap import CartoStyle, MaplibreBasemap
    from manywidgets import Column, Legend, Row, Text
    from manywidgets.lonboard import LayerToggle, MapFlyer

    lon_layers = []
    toggles = []
    for label, frame, color in (
        ("Events", layers.event_points, "#0072B2"),
        ("Hazards", layers.hazard_points, "#D55E00"),
        ("Impacts", layers.impact_points, "#009E73"),
    ):
        if frame.empty:
            continue
        prepared = frame.copy()
        if "color" not in prepared:
            prepared["color"] = color
        table, radii, colors = _arrow_points(prepared)
        if label == "Events" and layers.render_mode == "hexagon":
            layer = HeatmapLayer(table, pickable=True)
        else:
            layer = ScatterplotLayer(table, get_radius=radii, radius_units="pixels", get_fill_color=colors, get_line_color=[30, 30, 30, 255], line_width_min_pixels=1, stroked=True, pickable=True)
        lon_layers.append(layer)
        toggles.append(LayerToggle(layer, label=label))
    map_widget = Map(lon_layers, basemap=MaplibreBasemap(style=CartoStyle.Positron))
    locations = [
        {"label": str(row.event_title or row.family_key), "longitude": float(row.longitude), "latitude": float(row.latitude), "zoom": 8}
        for row in layers.event_points.head(50).itertuples()
    ]
    controls = [*toggles]
    if locations:
        controls.append(MapFlyer(map_widget, locations=locations, duration=1200, label="Fly to event"))
    legend_entries = [
        [hazard_color(label), label]
        for label in sorted(set(layers.event_points.get("hazard_label", pd.Series(dtype=str)).dropna()))
    ]
    return Column(Row(*controls), Legend(entries=legend_entries, title="Hazard"), Text(value=layers.disclaimer), map_widget)


def _asset(snapshot: EventSnapshotRow) -> tuple[str, Mapping[str, Any]] | None:
    for key, value in snapshot.assets.items():
        if not isinstance(value, Mapping):
            continue
        media_type = str(value.get("type") or "").lower()
        title = f"{key} {value.get('title') or ''}".lower()
        if "geo+json" in media_type or "geojson" in media_type or "map" in title:
            return str(key), value
    return None


def _positions(value: Any):
    if isinstance(value, list) and len(value) >= 2 and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value[:2]):
        yield value
    elif isinstance(value, list):
        for child in value:
            yield from _positions(child)


def validate_footprint_geojson(value: Any, *, max_features: int = 5_000) -> FootprintResult:
    if not isinstance(value, Mapping):
        return FootprintResult("invalid", "Footprint JSON must be an object.")
    if value.get("type") == "FeatureCollection":
        features = value.get("features")
    elif value.get("type") == "Feature":
        features = [value]
    else:
        return FootprintResult("invalid", "Footprint must be a GeoJSON Feature or FeatureCollection.")
    if not isinstance(features, list) or not features:
        return FootprintResult("invalid", "Footprint contains no features.")
    if len(features) > max_features:
        return FootprintResult("oversized", f"Footprint contains {len(features)} features; limit is {max_features}.")
    for feature in features:
        geometry = feature.get("geometry") if isinstance(feature, Mapping) else None
        if not isinstance(geometry, Mapping) or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            return FootprintResult("invalid", "Footprint contains a non-polygon geometry.")
        coordinates = list(_positions(geometry.get("coordinates")))
        if not coordinates or any(not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90) for point in coordinates):
            return FootprintResult("invalid", "Footprint contains invalid longitude/latitude coordinates.")
    return FootprintResult("available", "Validated PDC footprint.", value, feature_count=len(features))


def fetch_footprint(
    snapshot: EventSnapshotRow,
    *,
    cache_dir: Path = Path("data/cache/footprints"),
    max_bytes: int = 5_000_000,
    max_features: int = 5_000,
) -> FootprintResult:
    """Fetch one advertised selected-event footprint without sending API credentials."""

    selected = _asset(snapshot)
    if selected is None:
        return FootprintResult("missing", "No footprint-like asset was advertised for this event.")
    key, asset = selected
    href = asset.get("href")
    parsed = urlparse(str(href))
    if parsed.scheme != "https" or not parsed.hostname:
        return FootprintResult("invalid", "Footprint asset must use an absolute HTTPS URL.", asset_key=key)
    media_type = str(asset.get("type") or "").lower()
    if media_type and "json" not in media_type:
        return FootprintResult("invalid", f"Unsupported footprint media type: {media_type}", asset_key=key, host=parsed.hostname)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{sha256(str(href).encode('utf-8')).hexdigest()}.json"
    try:
        if cache_path.exists():
            payload = cache_path.read_bytes()
        else:
            request = Request(str(href), headers={"Accept": "application/geo+json, application/json", "User-Agent": "guard-pdc-explorer/0.1"})
            with open_without_redirects(request, timeout=30) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    return FootprintResult("oversized", f"Footprint exceeds {max_bytes} bytes.", asset_key=key, host=parsed.hostname)
                payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                return FootprintResult("oversized", f"Footprint exceeds {max_bytes} bytes.", asset_key=key, host=parsed.hostname)
        value = json.loads(payload)
    except HTTPError as error:
        if 300 <= error.code < 400:
            return FootprintResult("not_allowed", f"Footprint host answered with a redirect (HTTP {error.code}); redirects are not followed.", asset_key=key, host=parsed.hostname)
        status = "inaccessible" if error.code in {401, 403} else "failed"
        return FootprintResult(status, f"Footprint request returned HTTP {error.code}.", asset_key=key, host=parsed.hostname)
    except (URLError, OSError, http.client.HTTPException, UnicodeDecodeError, json.JSONDecodeError) as error:
        return FootprintResult("failed", f"Footprint could not be read: {type(error).__name__}.", asset_key=key, host=parsed.hostname)
    result = validate_footprint_geojson(value, max_features=max_features)
    if result.status == "available" and not cache_path.exists():
        temporary = cache_path.with_suffix(".tmp")
        temporary.write_bytes(payload)
        temporary.replace(cache_path)
    return FootprintResult(result.status, result.message, result.geojson, key, parsed.hostname, result.feature_count)
