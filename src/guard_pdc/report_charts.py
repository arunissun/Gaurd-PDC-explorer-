"""Static SVG charts for the Markdown and HTML evidence reports.

Plain SVG keeps the reports self-contained and offline (no image renderer or
browser is needed). Every chart counts distinct event families or shows one
event's own peak value; exposure is never summed across events.
"""

from __future__ import annotations

from html import escape
from typing import Sequence

import pandas as pd

from .analysis import country_event_counts
from .countries import country_name
from .models import MONTH_ABBREVIATIONS, QuerySpec
from .taxonomy import HAZARD_GROUP_ORDER, MEASURE_CATEGORY
from .theme import INK, INK_MUTED, INK_SECONDARY, PRIMARY, compact, hazard_color

FONT = "Arial, Helvetica, sans-serif"
WIDTH = 720


def _text(x: float, y: float, text: str, *, size: int = 12, color: str = INK, anchor: str = "start", weight: str = "normal") -> str:
    return (
        f"<text x='{x:.1f}' y='{y:.1f}' font-family='{FONT}' font-size='{size}' fill='{color}' "
        f"text-anchor='{anchor}' font-weight='{weight}'>{escape(text)}</text>"
    )


def _svg(height: float, title: str, body: list[str]) -> str:
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{WIDTH}' height='{height:.0f}' viewBox='0 0 {WIDTH} {height:.0f}' role='img'>"
        f"<title>{escape(title)}</title><rect width='100%' height='100%' fill='#FFFFFF'/>"
        + _text(16, 26, title, size=15, weight="bold")
        + "".join(body)
        + "</svg>"
    )


def horizontal_bars(title: str, labels: Sequence[str], values: Sequence[float], colors: Sequence[str] | None = None, *, note: str = "") -> str:
    """One labelled horizontal bar per row, value printed at the bar end."""

    label_width, top, row = 250, 46, 24
    plot = WIDTH - label_width - 70
    peak = max((float(value) for value in values), default=0.0) or 1.0
    body = []
    for index, (label, value) in enumerate(zip(labels, values, strict=True)):
        y = top + index * row
        length = max(float(value) / peak * plot, 1.0 if value else 0.0)
        color = colors[index] if colors else PRIMARY
        short = label if len(label) <= 38 else label[:37] + "…"
        body.append(_text(label_width - 8, y + 15, short, size=12, color=INK, anchor="end"))
        body.append(f"<rect x='{label_width}' y='{y + 3}' width='{length:.1f}' height='{row - 8}' rx='2' fill='{color}'/>")
        body.append(_text(label_width + length + 6, y + 15, compact(value), size=11, color=INK_SECONDARY))
    height = top + len(labels) * row + (34 if note else 14)
    if note:
        body.append(_text(16, height - 12, note, size=11, color=INK_MUTED))
    return _svg(height, title, body)


def vertical_bars(title: str, labels: Sequence[str], values: Sequence[float | None], *, note: str = "") -> str:
    """Vertical bars (e.g. one per month); None draws a gap marked 'not requested'."""

    left, top, plot_height = 46, 52, 180
    plot_width = WIDTH - left - 20
    step = plot_width / max(len(labels), 1)
    peak = max((float(value) for value in values if value is not None), default=0.0) or 1.0
    body = [f"<line x1='{left}' x2='{WIDTH - 20}' y1='{top + plot_height}' y2='{top + plot_height}' stroke='#C9CED6'/>"]
    for tick in (0, peak / 2, peak):
        y = top + plot_height - tick / peak * plot_height
        body.append(_text(left - 6, y + 4, compact(round(tick)), size=10, color=INK_MUTED, anchor="end"))
    label_every = max(1, round(len(labels) / 24))
    for index, (label, value) in enumerate(zip(labels, values, strict=True)):
        x = left + index * step
        if value is not None and value > 0:
            height = float(value) / peak * plot_height
            body.append(f"<rect x='{x + step * 0.15:.1f}' y='{top + plot_height - height:.1f}' width='{step * 0.7:.1f}' height='{height:.1f}' fill='{PRIMARY}'/>")
        if index % label_every == 0:
            body.append(_text(x + step / 2, top + plot_height + 16, label, size=10, color=INK_SECONDARY, anchor="middle"))
    height = top + plot_height + (48 if note else 30)
    if note:
        body.append(_text(16, height - 10, note, size=11, color=INK_MUTED))
    return _svg(height, title, body)


def report_charts(summary: pd.DataFrame, query: QuerySpec, selected_category: str = "people") -> dict[str, tuple[str, str]]:
    """``{file name: (caption, svg)}`` for an evidence report.

    Country detail: events per month, events per hazard and the largest events
    by the selected measure. All-country overview: events per month, events per
    hazard and the countries with the most events.
    """

    charts: dict[str, tuple[str, str]] = {}
    if summary.empty:
        return charts
    dated = summary.dropna(subset=["event_date"])
    windows = query.windows()
    multi_year = len(query.years) > 1
    labels = [f"{MONTH_ABBREVIATIONS[month - 1]}{' ' + str(year)[2:] if multi_year else ''}" for year, month in windows]
    counts = dated.groupby([dated["event_date"].dt.year, dated["event_date"].dt.month])["family_key"].nunique()
    values = [float(counts.get((year, month), 0)) for year, month in windows]
    caption = "Distinct events per month, by month of event start."
    charts["chart_events_by_month.svg"] = (caption, vertical_bars("Events per month", labels, values))

    by_hazard = summary.groupby("hazard_group")["family_key"].nunique()
    order = [group for group in [*HAZARD_GROUP_ORDER, "Other"] if group in by_hazard.index]
    caption = "Distinct events per hazard group."
    charts["chart_events_by_hazard.svg"] = (
        caption, horizontal_bars("Events by hazard", order, [float(by_hazard[group]) for group in order], [hazard_color(group) for group in order]),
    )

    if query.retrieves_impact_detail:
        measure = next((name for name, category in MEASURE_CATEGORY.items() if category == selected_category), None)
        column = f"{measure}_peak" if measure else None
        if column and column in summary:
            values = pd.to_numeric(summary[column], errors="coerce")
            top = summary.assign(_value=values)[values > 0].nlargest(10, "_value")
            if not top.empty:
                caption = f"Largest events by peak '{selected_category}' value (PDC exposure estimate, one value per event; never summed)."
                charts["chart_top_events.svg"] = (caption, horizontal_bars(
                    f"Largest events · {selected_category}", [str(title) for title in top["title"]], list(top["_value"]),
                    [hazard_color(group) for group in top["hazard_group"]],
                ))
    else:
        countries = country_event_counts(summary).head(15)
        if not countries.empty:
            caption = "Countries with the most distinct events. Multi-country events count in each country; totals must not be added."
            charts["chart_countries.svg"] = (caption, horizontal_bars(
                "Countries with the most events", [country_name(code) for code in countries["country_code"]],
                list(countries["event_families"].astype(float)),
            ))
    return charts
