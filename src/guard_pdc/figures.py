"""Analytical figures built from the event summary (one row per event family).

Every figure answers one question and carries no title of its own: the
interface's section header states the question, grain, period and the
exposure caveat. Values are PDC exposure estimates for the queried country;
categories are never added together and exposure is never summed across
events. Colour always follows the hazard group (theme.HAZARD_COLORS).
"""

from __future__ import annotations

from hashlib import sha256
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .analysis import exceedance
from .countries import country_name
from .taxonomy import HAZARD_GROUP_ORDER
from .theme import (
    CONTEXT,
    INK,
    INK_SECONDARY,
    PRIMARY,
    SURFACE,
    apply_plotly_theme,
    compact,
    empty_figure,
    exposure_size,
    hazard_color,
    log_axis,
    sequential_scale,
    with_alpha,
)

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MEASURE_LABELS = {
    "people": "People exposed",
    "households": "Households exposed",
    "schools": "Schools exposed",
    "hospitals": "Hospitals exposed",
    "capital": "Capital exposure (unit not stated)",
}
MEASURE_NOUNS = {"people": "people", "households": "households", "schools": "schools", "hospitals": "hospitals", "capital": "capital value"}


def _groups_present(data: pd.DataFrame) -> list[str]:
    present = set(data["hazard_group"].dropna()) if not data.empty else set()
    return [group for group in HAZARD_GROUP_ORDER if group in present]


def _short(title: Any, limit: int = 46) -> str:
    text = str(title or "Untitled")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _positive(summary: pd.DataFrame, measure: str) -> pd.DataFrame:
    column = f"{measure}_peak"
    if summary.empty or column not in summary:
        return summary.iloc[0:0]
    return summary[pd.to_numeric(summary[column], errors="coerce").gt(0)]


def _date(value: Any) -> str:
    return value.strftime("%d %b %Y") if isinstance(value, pd.Timestamp) and not pd.isna(value) else "date unknown"


def _hover_rows(data: pd.DataFrame, measure: str) -> np.ndarray:
    """customdata: family key, title, date, peak, latest, alert, hazard type."""

    return np.column_stack([
        data["family_key"].astype(str),
        data["title"].map(_short),
        data["event_date"].map(_date),
        data[f"{measure}_peak"].map(compact),
        data[f"{measure}_latest"].map(compact),
        data["alert_level_max"].fillna("not stated"),
        data["pdc_hazard_type"].fillna(data["hazard_group"]),
    ])


EVENT_HOVER = (
    "<b>%{customdata[1]}</b><br>%{customdata[6]} · %{customdata[2]}<br>"
    "Peak: %{customdata[3]} · Latest: %{customdata[4]}<br>Highest PDC alert: %{customdata[5]}<extra></extra>"
)


# --------------------------------------------------------------------- overview

def fig_seasonality(summary: pd.DataFrame, years: tuple[int, ...], months: tuple[int, ...] = tuple(range(1, 13))) -> go.Figure:
    """Month x year calendar of distinct event families (event start month)."""

    if summary.empty:
        return empty_figure("No events in the selected period.")
    counts = summary.dropna(subset=["year", "month"]).groupby(["year", "month"])["family_key"].nunique()
    z, text, hover = [], [], []
    for year in years:
        z_row, text_row, hover_row = [], [], []
        for month in range(1, 13):
            if month not in months:
                z_row.append(None)
                text_row.append("")
                hover_row.append(f"{MONTHS[month - 1]} {year}: not requested")
                continue
            value = int(counts.get((year, month), 0))
            z_row.append(value)
            text_row.append(str(value) if value else "")
            hover_row.append(f"{MONTHS[month - 1]} {year}: {value} event{'s' if value != 1 else ''}")
        z.append(z_row)
        text.append(text_row)
        hover.append(hover_row)
    peak = max((value for row in z for value in row if value is not None), default=0) or 1
    scale = sequential_scale()
    # Cell labels use the heatmap's own text (automatic contrast). Annotations
    # would place a numeric-looking year label by value, not by category.
    fig = go.Figure(go.Heatmap(
        z=z, x=list(MONTHS), y=[str(year) for year in years], zmin=0, zmax=peak, colorscale=scale,
        xgap=3, ygap=3, showscale=False, customdata=hover, hovertemplate="%{customdata}<extra></extra>",
        text=text, texttemplate="%{text}", textfont=dict(size=12),
    ))
    fig.update_yaxes(autorange="reversed", type="category", showgrid=False, fixedrange=True)
    fig.update_xaxes(side="top", showgrid=False, fixedrange=True)
    return apply_plotly_theme(fig, height=64 + 40 * len(years))


def fig_events_by_period(summary: pd.DataFrame, years: tuple[int, ...]) -> go.Figure:
    """Distinct events per year (or per month for one year), stacked by hazard."""

    if summary.empty:
        return empty_figure("No events in the selected period.")
    yearly = len(years) > 1
    if yearly:
        data = summary.dropna(subset=["year"]).assign(period=lambda frame: frame["year"].astype(int).astype(str))
        categories = [str(year) for year in years]
    else:
        data = summary.dropna(subset=["month"]).assign(period=lambda frame: frame["month"].astype(int).map(lambda month: MONTHS[month - 1]))
        categories = list(MONTHS)
    counts = data.groupby(["period", "hazard_group"])["family_key"].nunique()
    fig = go.Figure()
    for group in _groups_present(data):
        values = [int(counts.get((period, group), 0)) for period in categories]
        fig.add_bar(
            x=categories, y=values, name=group, marker=dict(color=hazard_color(group), line=dict(color=SURFACE, width=1.5)),
            hovertemplate=f"%{{x}} · {group}: %{{y}} events<extra></extra>",
        )
    totals = [int(data[data["period"] == period]["family_key"].nunique()) for period in categories]
    fig.add_scatter(
        x=categories, y=totals, mode="text", text=[str(total) if total else "" for total in totals], textposition="top center",
        textfont=dict(color=INK_SECONDARY, size=11), showlegend=False, hoverinfo="skip",
    )
    fig.update_layout(barmode="stack", legend=dict(traceorder="normal"))
    fig.update_xaxes(type="category", categoryorder="array", categoryarray=categories, showgrid=False)
    fig.update_yaxes(title="Distinct events", rangemode="tozero", range=[0, max(totals + [1]) * 1.15])
    return apply_plotly_theme(fig, height=340)


MIN_BOX_EVENTS = 5
# Category tick labels are wrapped to this many characters per line so that
# neighbouring hazard names do not run into each other on narrow screens.
LABEL_LINE_CHARS = 12


def wrap_label(text: str, width: int = LABEL_LINE_CHARS) -> str:
    """Break a label into <br>-separated lines of at most ``width`` characters.

    Words are never split; a single word longer than ``width`` keeps its own line.
    """

    lines: list[str] = []
    for word in str(text).split():
        if lines and len(lines[-1]) + 1 + len(word) <= width:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    return "<br>".join(lines)


def fig_hazard_profile(summary: pd.DataFrame, measure: str = "people", *, height: int = 420) -> go.Figure:
    """Box plot of peak exposure per event, one box per hazard group (log scale).

    Box = middle half of events, dark line = median, whiskers = smallest to
    largest event. Groups with fewer than MIN_BOX_EVENTS events show only a
    range line and the median. Only events with a positive value are drawn (log scale);
    each label states how many of the hazard's events that is. Groups are
    ordered by median, highest first. Hover a box for its exact statistics.
    """

    data = _positive(summary, measure)
    if data.empty:
        return empty_figure(f"No event had {MEASURE_NOUNS[measure]} exposed.", height=height)
    column = f"{measure}_peak"
    totals = summary.groupby("hazard_group")["family_key"].nunique()
    # Quartiles are computed on log10 values (a linear axis labelled in real
    # units); order statistics are unchanged by the log transform.
    data = data.assign(_log=np.log10(pd.to_numeric(data[column]).astype(float)))
    medians = data.groupby("hazard_group")["_log"].median().sort_values(ascending=False)
    labels = {}
    for group in medians.index:
        count, total = int((data["hazard_group"] == group).sum()), int(totals.get(group, 0))
        detail = f"{count} events" if count == total else f"{count} of {total}<br>events"
        labels[group] = f"{wrap_label(group)}<br><span style='font-size:10.5px;color:{INK_SECONDARY}'>{detail}</span>"
    fig = go.Figure()
    for group in medians.index:
        values = data.loc[data["hazard_group"] == group, "_log"]
        color = hazard_color(group)
        q1, median, q3 = values.quantile([0.25, 0.5, 0.75])
        if len(values) >= MIN_BOX_EVENTS:
            fig.add_trace(go.Box(
                x=[labels[group]] * len(values), y=values, name=group, width=0.45, boxpoints=False,
                fillcolor=with_alpha(color, 0.28), line=dict(color=color, width=1.8), whiskerwidth=0.4,
                hoveron="boxes", hoverinfo="skip", showlegend=False,
            ))
        else:
            # Too few events for quartiles: show only their range and median.
            fig.add_trace(go.Scatter(
                x=[labels[group], labels[group]], y=[values.min(), values.max()], mode="lines", line=dict(color=color, width=2.5),
                hoverinfo="skip", showlegend=False,
            ))
        fig.add_trace(go.Scatter(
            x=[labels[group]], y=[median], mode="markers", marker=dict(symbol="line-ew", size=34, line=dict(width=3, color=INK)),
            hovertemplate=(
                f"<b>{group}</b><br>Median: {compact(10 ** median)} {MEASURE_NOUNS[measure]} per event<br>"
                f"Middle half: {compact(10 ** q1)} – {compact(10 ** q3)}<br>"
                f"Range: {compact(10 ** values.min())} – {compact(10 ** values.max())}<extra></extra>"
            ),
            showlegend=False,
        ))
    low, high = float(data["_log"].min()), float(data["_log"].max())
    ticks = list(range(math.floor(low), math.ceil(high) + 1))
    fig.update_yaxes(
        tickvals=ticks, ticktext=[compact(10**power) for power in ticks],
        range=[low - 0.25, high + 0.25], title=MEASURE_LABELS[measure] + " per event (peak, log scale)",
    )
    fig.update_xaxes(type="category", categoryorder="array", categoryarray=[labels[group] for group in medians.index], showgrid=False, title=None,
                     tickfont=dict(size=10.5, color=INK), tickangle=0, automargin=True)
    fig.update_layout(boxgap=0.3)
    return apply_plotly_theme(fig, height=height)


# Size classes per measure (upper edges of the three lower classes). People and
# households span millions; schools and hospitals rarely exceed hundreds.
SIZE_CLASS_EDGES = {
    "people": (1e4, 1e5, 1e6),
    "households": (1e3, 1e4, 1e5),
    "schools": (10, 100, 1000),
    "hospitals": (5, 25, 100),
    "capital": (1e6, 1e8, 1e9),
}
# Ramp positions for the four classes, smallest to largest (theme.SEQUENTIAL).
_CLASS_COLORS = ("#F8B878", "#E57B4A", "#B93E3B", "#561C3A")
NO_VALUE_LABEL = "Zero or no value"


def size_class_labels(measure: str) -> tuple[str, ...]:
    low, mid, high = SIZE_CLASS_EDGES[measure]
    return (f"Under {compact(low)}", f"{compact(low)}–{compact(mid)}", f"{compact(mid)}–{compact(high)}", f"{compact(high)} or more")


def size_class(values: pd.Series, measure: str) -> pd.Series:
    """Readable size class of each event's peak value (NO_VALUE_LABEL for zero/missing)."""

    labels = size_class_labels(measure)
    low, mid, high = SIZE_CLASS_EDGES[measure]
    numbers = pd.to_numeric(values, errors="coerce")
    return pd.Series(np.select(
        [numbers.isna() | numbers.le(0), numbers.lt(low), numbers.lt(mid), numbers.lt(high)],
        [NO_VALUE_LABEL, labels[0], labels[1], labels[2]], default=labels[3],
    ), index=values.index)


def fig_impact_timeseries(
    summary: pd.DataFrame, measure: str, years: tuple[int, ...], months: tuple[int, ...] = tuple(range(1, 13)), *, split: str = "size",
    period: tuple[pd.Timestamp, pd.Timestamp] | None = None,
) -> go.Figure:
    """Events per month over the whole period as a stacked area.

    ``period`` (first and last month shown) zooms the time axis; the interface
    sets it from a plain slider above the chart.

    ``split="size"`` stacks events by the size class of their peak value (largest
    class at the bottom); ``split="hazard"`` stacks by hazard group. Exposure is
    never summed across events: repeated PDC alerts would count the same people
    many times. The unified hover names each month's largest event.
    """

    data = summary.dropna(subset=["event_date"])
    if data.empty:
        return empty_figure("No dated events in this result.")
    periods = pd.period_range(f"{years[0]}-01", f"{years[-1]}-12", freq="M")
    x = periods.to_timestamp()
    requested = np.array([period.month in months for period in periods])
    data = data.assign(_period=data["event_date"].dt.tz_convert(None).dt.to_period("M"))
    noun = MEASURE_NOUNS[measure]
    if split == "hazard":
        data = data.assign(_class=data["hazard_group"])
        categories = _groups_present(data) + ([group for group in ("Other",) if group in set(data["hazard_group"])])
        colors = {group: hazard_color(group) for group in categories}
    else:
        data = data.assign(_class=size_class(data[f"{measure}_peak"], measure))
        labels = size_class_labels(measure)
        categories = [*labels[::-1], NO_VALUE_LABEL]
        colors = {**dict(zip(labels, _CLASS_COLORS, strict=True)), NO_VALUE_LABEL: "#D5D9E0"}
        categories = [category for category in categories if category in set(data["_class"])]
    counts = data.groupby(["_class", "_period"])["family_key"].nunique()
    fig = go.Figure()
    for category in categories:
        values = np.array([counts.get((category, period), 0) for period in periods], dtype=float)
        values[~requested] = np.nan
        name = category if split == "hazard" else (f"{category} {noun}" if category != NO_VALUE_LABEL else category)
        fig.add_scatter(
            x=x, y=values, name=name, mode="lines", stackgroup="events", line=dict(width=0.8, color=colors[category], shape="linear"),
            fillcolor=with_alpha(colors[category], 0.88), hovertemplate="%{y:.0f}<extra>" + name + "</extra>",
        )
    # Hover-only trace: the month's total and its largest event (never a sum of exposure).
    totals = data.groupby("_period")["family_key"].nunique()
    peaks = pd.to_numeric(data[f"{measure}_peak"], errors="coerce")
    largest = data.assign(_value=peaks).dropna(subset=["_value"]).sort_values("_value").groupby("_period").tail(1).set_index("_period")
    hover = [
        f"{int(totals.get(period, 0))} events" + (f" · largest: {_short(largest.loc[period, 'title'], 38)} ({compact(largest.loc[period, '_value'])} {noun})" if period in largest.index else "")
        if requested[index] else "month not requested"
        for index, period in enumerate(periods)
    ]
    fig.add_scatter(x=x, y=[float(totals.get(period, 0)) if requested[index] else None for index, period in enumerate(periods)], mode="lines",
                    line=dict(width=0, color="rgba(0,0,0,0)"), customdata=hover, hovertemplate="%{customdata}<extra>Month</extra>", showlegend=False)
    fig.update_layout(hovermode="x unified", legend=dict(traceorder="normal", font=dict(size=12, color=INK), itemsizing="constant"))
    fig.update_yaxes(title="Events per month", rangemode="tozero", fixedrange=True)
    if len(years) > 1:
        fig.update_xaxes(type="date", tickformat="%b %Y", showgrid=False)
        for year in years[1:]:
            fig.add_vline(x=pd.Timestamp(f"{year}-01-01").timestamp() * 1000 - 15 * 86400000, line=dict(color="#C9CED6", width=1, dash="dot"))
        if period is not None:
            fig.update_xaxes(range=[period[0] - pd.Timedelta(days=12), period[1] + pd.Timedelta(days=12)])
        return apply_plotly_theme(fig, height=400)
    fig.update_xaxes(type="date", tickformat="%b", dtick="M1", showgrid=False)
    return apply_plotly_theme(fig, height=360)


# Line colour per measure for the separate exposure time series.
MEASURE_COLORS = {"people": "#0B7A80", "households": "#6B4BA1", "schools": "#D0611E", "hospitals": "#B8312F", "capital": "#55657A"}


def fig_measure_timeseries(
    summary: pd.DataFrame, measure: str, years: tuple[int, ...], months: tuple[int, ...] = tuple(range(1, 13)),
    *, period: tuple[pd.Timestamp, pd.Timestamp] | None = None, height: int = 280,
) -> go.Figure:
    """Largest single event per month for one measure (one chart per measure).

    Each point is the peak value of the month's largest event (by event start),
    never a sum across events; the hover names that event and its hazard.
    Months without events read zero; unrequested months are gaps.
    """

    column = f"{measure}_peak"
    data = summary.dropna(subset=["event_date"])
    if data.empty or column not in summary:
        return empty_figure("No dated events in this result.", height=height)
    color = MEASURE_COLORS.get(measure, PRIMARY)
    periods = pd.period_range(f"{years[0]}-01", f"{years[-1]}-12", freq="M")
    x = periods.to_timestamp()
    requested = np.array([month.month in months for month in periods])
    data = data.assign(_period=data["event_date"].dt.tz_convert(None).dt.to_period("M"), _value=pd.to_numeric(data[column], errors="coerce"))
    largest = data.dropna(subset=["_value"]).sort_values("_value").groupby("_period").tail(1).set_index("_period")
    y, sizes, hover = [], [], []
    for index, month in enumerate(periods):
        if not requested[index]:
            y.append(None)
            sizes.append(0)
            hover.append("month not requested")
        elif month in largest.index:
            event = largest.loc[month]
            y.append(float(event["_value"]))
            sizes.append(7)
            hover.append(f"<b>{_short(event['title'], 44)}</b><br>{event['hazard_group']} · {compact(event['_value'])} {MEASURE_NOUNS[measure]}")
        else:
            y.append(0.0)
            sizes.append(0)  # no dot for months without events
            hover.append("no event with a value")
    fig = go.Figure(go.Scatter(
        x=x, y=y, mode="lines+markers", line=dict(color=color, width=2), fill="tozeroy", fillcolor=with_alpha(color, 0.12),
        marker=dict(color=color, size=sizes, line=dict(color=SURFACE, width=1)), customdata=hover,
        hovertemplate="%{x|%b %Y}<br>%{customdata}<extra></extra>", showlegend=False, connectgaps=False,
    ))
    fig.update_yaxes(title=MEASURE_LABELS[measure], exponentformat="B", rangemode="tozero", fixedrange=True)
    tick = dict(tickformat="%b %Y", showgrid=False) if len(years) > 1 else dict(tickformat="%b", dtick="M1", showgrid=False)
    fig.update_xaxes(type="date", **tick)
    if period is not None:
        fig.update_xaxes(range=[period[0] - pd.Timedelta(days=12), period[1] + pd.Timedelta(days=12)])
    return apply_plotly_theme(fig, height=height)


# ----------------------------------------------------------------------- events

def fig_top_events(summary: pd.DataFrame, measure: str = "people", n: int = 15) -> go.Figure:
    """Largest events by peak value of one measure."""

    data = _positive(summary, measure)
    if data.empty:
        return empty_figure(f"No positive {MEASURE_NOUNS[measure]} values in this result.")
    column = f"{measure}_peak"
    top = data.nlargest(n, column).iloc[::-1]
    keys = list(top["family_key"].astype(str))
    labels = [f"{_short(title, 40)} · {_date(date)}" for title, date in zip(top["title"], top["event_date"], strict=False)]
    fig = go.Figure()
    for group in _groups_present(top):
        rows = top[top["hazard_group"] == group]
        # y = event family key (unique), so two events with the same PDC title
        # and date keep separate rows; tick text shows the readable label.
        fig.add_bar(
            x=rows[column], y=rows["family_key"].astype(str), orientation="h", name=group, marker=dict(color=hazard_color(group)),
            text=rows[column].map(compact), textposition="outside", textfont=dict(color=INK_SECONDARY, size=11), cliponaxis=False,
            customdata=_hover_rows(rows, measure), hovertemplate=EVENT_HOVER,
        )
    fig.update_yaxes(type="category", categoryorder="array", categoryarray=keys, tickvals=keys, ticktext=labels, showgrid=False,
                     tickfont=dict(size=11, color=INK))
    fig.update_xaxes(title=MEASURE_LABELS[measure] + " (peak)", rangemode="tozero", exponentformat="B", range=[0, float(top[column].max()) * 1.15])
    # One event per row: stacking the per-hazard traces keeps every bar full thickness.
    fig.update_layout(barmode="stack", bargap=0.3, clickmode="event+select")
    return apply_plotly_theme(fig, height=80 + 30 * len(top))


def _lane_jitter(key: str) -> float:
    return (int(sha256(key.encode("utf-8")).hexdigest()[:6], 16) / 0xFFFFFF - 0.5) * 0.6


def fig_timeline(summary: pd.DataFrame, measure: str = "people") -> go.Figure:
    """Every event on a time axis, one lane per hazard group, sized by exposure class."""

    data = summary.dropna(subset=["event_date"])
    if data.empty:
        return empty_figure("No dated events in this result.")
    groups = _groups_present(data)
    lanes = {group: index for index, group in enumerate(reversed(groups))}
    column = f"{measure}_peak"
    sizes = pd.to_numeric(data[column], errors="coerce").map(exposure_size)
    fig = go.Figure()
    for group in groups:
        rows = data[data["hazard_group"] == group]
        y = [lanes[group] + _lane_jitter(key) for key in rows["family_key"]]
        fig.add_scatter(
            x=rows["event_date"], y=y, mode="markers", name=group,
            marker=dict(color=hazard_color(group), size=sizes[rows.index], opacity=0.8, line=dict(color=SURFACE, width=0.8)),
            customdata=_hover_rows(rows, measure), hovertemplate=EVENT_HOVER,
        )
    fig.update_yaxes(tickvals=list(lanes.values()), ticktext=list(lanes.keys()), range=[-0.6, len(lanes) - 0.4], showgrid=False, zeroline=False)
    fig.update_xaxes(showgrid=True, title=None)
    fig.update_layout(showlegend=False, clickmode="event+select")
    return apply_plotly_theme(fig, height=90 + 46 * len(groups))


# --------------------------------------------------------------------- exposure

def fig_exceedance(summary: pd.DataFrame, measure: str = "people", by: str = "hazard_group", colors: Mapping[str, str] | None = None) -> go.Figure:
    """How many events exposed at least X (peak values; zero excluded)."""

    curve = exceedance(summary, measure, by)
    if curve.empty:
        return empty_figure(f"No positive {MEASURE_NOUNS[measure]} values to draw.")
    order = [group for group in HAZARD_GROUP_ORDER if group in set(curve[by])] if by == "hazard_group" else list(dict.fromkeys(curve[by]))
    fig = go.Figure()
    for group in order:
        rows = curve[curve[by] == group].sort_values("threshold")
        color = (colors or {}).get(group) or hazard_color(group)
        label = country_name(group) if by == "country" else str(group)
        fig.add_scatter(
            x=rows["threshold"], y=rows["events"], mode="lines", line=dict(color=color, width=2, shape="vh"), name=label,
            customdata=rows["threshold"].map(compact), hovertemplate=f"{label}: %{{y}} events with ≥ %{{customdata}}<extra></extra>",
        )
    fig.update_xaxes(**log_axis(title=f"At least this many {MEASURE_NOUNS[measure]} (event peak)"))
    # Log count axis (frequency-severity convention) keeps small hazard groups
    # readable next to large ones.
    top = float(curve["events"].max())
    ticks = [value for value in (1, 3, 10, 30, 100, 300, 1000, 3000, 10000, 30000) if value <= top * 1.5]
    fig.update_yaxes(title="Number of events (log scale)", type="log", tickvals=ticks, ticktext=[compact(value) for value in ticks],
                     range=[math.log10(0.8), math.log10(top * 1.4)])
    return apply_plotly_theme(fig, height=360)


def fig_distribution(summary: pd.DataFrame, measure: str = "people", kind: str = "histogram") -> go.Figure:
    """Distribution of peak values: log-binned histogram, ECDF, or box by hazard."""

    data = _positive(summary, measure)
    if data.empty:
        return empty_figure(f"No positive {MEASURE_NOUNS[measure]} values to draw.")
    column = f"{measure}_peak"
    if kind == "box":
        return fig_hazard_profile(summary, measure)
    values = np.sort(data[column].to_numpy(dtype=float))
    fig = go.Figure()
    if kind == "ecdf":
        fig.add_scatter(x=values, y=np.arange(1, len(values) + 1) / len(values), mode="lines", line=dict(color=PRIMARY, width=2, shape="hv"),
                        customdata=[compact(value) for value in values], hovertemplate="%{y:.0%} of events ≤ %{customdata}<extra></extra>", showlegend=False)
        fig.update_xaxes(**log_axis(title=MEASURE_LABELS[measure] + " (event peak)"))
        fig.update_yaxes(title="Share of events", tickformat=".0%", range=[0, 1.02])
        return apply_plotly_theme(fig, height=320)
    # Readable 1-3-10 bins (1k–3k, 3k–10k, ...), roughly half a decade each.
    low_decade, high_decade = math.floor(math.log10(values.min())), math.ceil(math.log10(values.max()))
    edges = np.array(sorted({base * 10.0 ** power for power in range(low_decade, high_decade + 1) for base in (1, 3)}))
    edges = edges[(edges <= values.max() * 3) & (edges * 3 >= values.min())]
    edges = np.append(edges, edges[-1] * (10 / 3 if str(int(edges[-1]))[0] == "3" else 3))
    counts, edges = np.histogram(values, bins=edges)
    labels = [f"{compact(low)}–{compact(high)}" for low, high in zip(edges[:-1], edges[1:], strict=False)]
    keep = np.flatnonzero(counts)
    labels, counts = [labels[index] for index in range(keep.min(), keep.max() + 1)], counts[keep.min(): keep.max() + 1]
    fig.add_bar(x=labels, y=counts, marker=dict(color=PRIMARY), hovertemplate="%{x}: %{y} events<extra></extra>", showlegend=False)
    fig.update_xaxes(type="category", title=MEASURE_LABELS[measure] + " (event peak)", showgrid=False)
    fig.update_yaxes(title="Number of events", rangemode="tozero")
    return apply_plotly_theme(fig, height=320)


def infrastructure_stats(summary: pd.DataFrame, measure: str) -> dict[str, int]:
    column = f"{measure}_peak"
    values = pd.to_numeric(summary.get(column, pd.Series(dtype=float)), errors="coerce")
    return {"with_value": int(values.notna().sum()), "with_any": int(values.gt(0).sum()), "zero": int(values.eq(0).sum())}


def fig_infrastructure(summary: pd.DataFrame, measure: str, n: int = 8) -> go.Figure:
    """Top events for one non-population measure, on its own axis."""

    data = _positive(summary, measure)
    if data.empty:
        return empty_figure(f"No event had {MEASURE_NOUNS[measure]} exposed.", height=200)
    column = f"{measure}_peak"
    top = data.nlargest(n, column).iloc[::-1]
    keys = list(top["family_key"].astype(str))
    labels = [f"{_short(title, 34)} · {_date(date)}" for title, date in zip(top["title"], top["event_date"], strict=False)]
    fig = go.Figure(go.Bar(
        x=top[column], y=keys, orientation="h", marker=dict(color=[hazard_color(group) for group in top["hazard_group"]]),
        text=top[column].map(compact), textposition="outside", textfont=dict(color=INK_SECONDARY, size=11), cliponaxis=False,
        customdata=_hover_rows(top, measure), hovertemplate=EVENT_HOVER, showlegend=False,
    ))
    fig.update_yaxes(type="category", categoryorder="array", categoryarray=keys, tickvals=keys, ticktext=labels, showgrid=False, tickfont=dict(size=10.5))
    fig.update_xaxes(title=MEASURE_LABELS[measure] + " (peak)", exponentformat="B", rangemode="tozero", range=[0, float(top[column].max()) * 1.2])
    return apply_plotly_theme(fig, height=60 + 28 * len(top))


# ----------------------------------------------------------------- event detail

# ---------------------------------------------------------------------- compare

def country_colors(countries: list[str]) -> dict[str, str]:
    """Country identity colours for the Compare tab (validated slot order)."""

    slots = ("#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7", "#e34948", "#eda100", "#e87ba4", "#008300")
    return {country: slots[index % len(slots)] for index, country in enumerate(countries)}


def fig_compare_per_year(summaries: Mapping[str, pd.DataFrame], years: tuple[int, ...]) -> go.Figure:
    """Distinct events per year for each country, stacked by hazard, shared y-axis."""

    countries = list(summaries)
    if not countries:
        return empty_figure("No country results loaded.")
    fig = make_subplots(rows=1, cols=len(countries), shared_yaxes=True, subplot_titles=[country_name(country) for country in countries], horizontal_spacing=0.03)
    seen: set[str] = set()
    categories = [str(year) for year in years] if len(years) > 1 else list(MONTHS)
    for column, country in enumerate(countries, start=1):
        data = summaries[country]
        if data.empty:
            continue
        if len(years) > 1:
            data = data.dropna(subset=["year"]).assign(period=lambda frame: frame["year"].astype(int).astype(str))
        else:
            data = data.dropna(subset=["month"]).assign(period=lambda frame: frame["month"].astype(int).map(lambda month: MONTHS[month - 1]))
        counts = data.groupby(["period", "hazard_group"])["family_key"].nunique()
        for group in _groups_present(data):
            fig.add_bar(
                x=categories, y=[int(counts.get((period, group), 0)) for period in categories], name=group, legendgroup=group,
                showlegend=group not in seen, marker=dict(color=hazard_color(group), line=dict(color=SURFACE, width=1)),
                hovertemplate=f"{country_name(country)} %{{x}} · {group}: %{{y}} events<extra></extra>", row=1, col=column,
            )
            seen.add(group)
    fig.update_layout(barmode="stack", legend=dict(traceorder="normal"))
    fig.update_xaxes(type="category", showgrid=False, tickangle=0)
    fig.update_yaxes(title_text="Distinct events", row=1, col=1)
    for annotation in fig.layout.annotations:
        annotation.font = dict(size=13, color=INK)
    fig = apply_plotly_theme(fig, height=360)
    # Country names sit above each panel, so the hazard legend goes below.
    fig.update_layout(legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="left", x=0), margin=dict(t=28, b=8))
    return fig


def fig_compare_hazard_mix(summaries: Mapping[str, pd.DataFrame], measure: str = "people") -> go.Figure:
    """Country x hazard grid of distinct events; median peak exposure on hover."""

    rows = []
    for country, data in summaries.items():
        for group, frame in data.groupby("hazard_group"):
            median = pd.to_numeric(frame[f"{measure}_peak"], errors="coerce").median()
            rows.append((country, group, frame["family_key"].nunique(), median))
    if not rows:
        return empty_figure("No events in the loaded countries.")
    table = pd.DataFrame(rows, columns=["country", "group", "events", "median"])
    groups = [group for group in HAZARD_GROUP_ORDER if group in set(table["group"])]
    countries = list(summaries)
    names = [country_name(country) for country in countries]
    z = [[int(table[(table.country == c) & (table.group == g)]["events"].sum()) for g in groups] for c in countries]
    medians = [[compact(table[(table.country == c) & (table.group == g)]["median"].iloc[0]) if not table[(table.country == c) & (table.group == g)].empty else "–" for g in groups] for c in countries]
    peak = max(max(row) for row in z) or 1
    scale = sequential_scale()
    text = [[str(value) if value else "" for value in row] for row in z]
    fig = go.Figure(go.Heatmap(z=z, x=groups, y=names, colorscale=scale, zmin=0, zmax=peak, xgap=3, ygap=3, showscale=False,
                               text=text, texttemplate="%{text}", textfont=dict(size=12),
                               customdata=medians, hovertemplate="%{y} · %{x}: %{z} events<br>Median peak " + MEASURE_NOUNS[measure] + ": %{customdata}<extra></extra>"))
    fig.update_yaxes(autorange="reversed", type="category", showgrid=False, fixedrange=True)
    fig.update_xaxes(side="top", type="category", showgrid=False, tickangle=0, fixedrange=True)
    return apply_plotly_theme(fig, height=90 + 46 * len(countries))


# ---------------------------------------------------------------------- quality

def fig_coverage(coverage: pd.DataFrame) -> go.Figure:
    """PDC event snapshots retrieved per year: separates coverage from hazard trends."""

    if coverage.empty:
        return empty_figure("No coverage information.", height=200)
    fig = go.Figure()
    fig.add_bar(x=coverage["year"].astype(str), y=coverage["event_snapshots"], name="Event snapshots", marker=dict(color=CONTEXT),
                hovertemplate="%{x}: %{y} event snapshots<extra></extra>")
    fig.add_bar(x=coverage["year"].astype(str), y=coverage["event_families"], name="Distinct events", marker=dict(color=PRIMARY),
                hovertemplate="%{x}: %{y} distinct events<extra></extra>")
    fig.update_layout(barmode="group")
    fig.update_xaxes(type="category", showgrid=False)
    fig.update_yaxes(title="Count", rangemode="tozero")
    return apply_plotly_theme(fig, height=260)

