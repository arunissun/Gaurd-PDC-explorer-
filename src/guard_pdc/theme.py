"""Shared visual tokens and the Plotly theme for every PDC figure and map.

One source for colours, typography, spacing and number formatting so the
dashboard (and later the notebook) read as one product. The hazard palette is
the validated eight-slot categorical order (CVD-checked against a white
surface: worst adjacent ΔE 9.1); hazard groups are assigned in that fixed order
and never re-coloured by rank. Three slots sit below 3:1 contrast on white, so
every chart that uses them also carries a legend or direct labels and a table.
"""

from __future__ import annotations

import math
from typing import Any

import plotly.graph_objects as go
import plotly.io as pio

from .taxonomy import HAZARD_GROUP_ORDER, OTHER_HAZARD_GROUP


# Surfaces and ink -----------------------------------------------------------
PAGE = "#F5F6F8"
SURFACE = "#FFFFFF"
BORDER = "#E2E5EA"
INK = "#1B2533"
INK_SECONDARY = "#4A5565"
INK_MUTED = "#7A8494"
GRID = "#EDEFF2"
AXIS = "#C9CED6"
# Accent for single-series charts, selection and controls: deep teal (5.0:1 on
# white). Blue is reserved for the Flood hazard colour.
PRIMARY = "#0B7A80"
PRIMARY_LIGHT = "#CFE8E8"
# Neutral for context bars (for example, snapshots behind distinct events).
CONTEXT = "#C7CDD6"
FONT = 'Inter, "Segoe UI", system-ui, -apple-system, sans-serif'
SPACE = (8, 16, 24, 32, 48)

# Hazard groups in validated colour-slot order (taxonomy.HAZARD_GROUPS order).
_HAZARD_SLOTS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948")
HAZARD_COLORS = {group: color for group, color in zip(HAZARD_GROUP_ORDER, _HAZARD_SLOTS, strict=False)}
HAZARD_COLORS[OTHER_HAZARD_GROUP] = "#9AA1AB"

# Sequential (magnitude) ramp for counts and intensity: cream -> amber -> red ->
# deep plum, monotone in lightness, read as "more activity". Used by the
# calendar, density map and choropleth; never encodes hazard type.
SEQUENTIAL = ("#FFF5E1", "#FDDCAA", "#F8B878", "#F08C55", "#DD623F", "#B93E3B", "#88293D", "#561C3A")
SEQUENTIAL_BLUE = SEQUENTIAL  # retained name for older callers


def sequential_scale(*, transparent_start: bool = False) -> list[list[Any]]:
    """Plotly colourscale from SEQUENTIAL; optionally fade the lowest stop out."""

    scale = [[index / (len(SEQUENTIAL) - 1), color] for index, color in enumerate(SEQUENTIAL)]
    if transparent_start:
        scale[0] = [0.0, "rgba(255,245,225,0)"]
    return scale


# PDC alert levels are ordinal and follow PDC's own convention
# (information, advisory = yellow, watch = orange, warning = red).
ALERT_COLORS = {"INFORMATION": "#98A3B3", "ADVISORY": "#E9B949", "WATCH": "#E07B39", "WARNING": "#B8312F"}

# Evidence states (status tiles). Always shown with a text label.
STATUS_STYLE = {
    "present_positive": ("Present", "#0B7A80", "#FFFFFF"),
    "present_zero": ("Zero", "#CFE8E8", "#075459"),
    "missing": ("Missing", "#EEF0F3", "#4A5565"),
    "unavailable": ("Unavailable", "#FFF4DB", "#7A5200"),
    "conflicting": ("Conflict", "#FBE3E3", "#A12A2A"),
    "not_retrieved": ("Not retrieved", "#FFFFFF", "#7A8494"),
}

# Marker diameter (px). Exposure spans five or more orders of magnitude, so the
# diameter grows by a fixed step per tenfold increase (log scale): 1k -> 4 px,
# 1M -> 18 px, 100M -> 27 px. Zero and missing values get the smallest dot.
SIZE_MIN = 4.0
SIZE_PER_DECADE = 4.6
SIZE_MAX = 30.0
SIZE_LEGEND_VALUES = (1e3, 1e4, 1e5, 1e6, 1e7, 1e8)
NO_VALUE_SIZE = SIZE_MIN
# Retained for callers that still size by exposure class (diameter, px).
CLASS_SIZES = {"Zero": 4, "Under 10k": 6, "10k–100k": 11, "100k–1M": 16, "1M or more": 22}


def exposure_size(value: Any) -> float:
    """Marker diameter for one exposure value (log-scaled, bounded)."""

    if value is None or (isinstance(value, float) and math.isnan(value)):
        return NO_VALUE_SIZE
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return SIZE_MIN
    return min(SIZE_MAX, max(SIZE_MIN, SIZE_MIN + SIZE_PER_DECADE * (math.log10(number) - 3)))


def with_alpha(color: str, alpha: float) -> str:
    """'#RRGGBB' -> 'rgba(r,g,b,a)'."""

    color = color.lstrip("#")
    return f"rgba({int(color[0:2], 16)},{int(color[2:4], 16)},{int(color[4:6], 16)},{alpha})"


def hazard_color(group: str) -> str:
    return HAZARD_COLORS.get(group, HAZARD_COLORS[OTHER_HAZARD_GROUP])


def compact(value: Any, *, digits: int = 1) -> str:
    """Human number: 1.2M, 340k, 12. Missing values read as an en dash."""

    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    number = float(value)
    sign = "-" if number < 0 else ""
    number = abs(number)
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "k")):
        if number >= threshold:
            text = f"{number / threshold:.{digits}f}".rstrip("0").rstrip(".")
            return f"{sign}{text}{suffix}"
    if number == int(number):
        return f"{sign}{int(number):,}"
    return f"{sign}{number:,.1f}"


LOG_TICKS = [10**power for power in range(0, 13)]
LOG_TICK_TEXT = [compact(value) for value in LOG_TICKS]


def log_axis(**overrides: Any) -> dict[str, Any]:
    """Axis settings for a log scale with human tick labels."""

    return {"type": "log", "tickvals": LOG_TICKS, "ticktext": LOG_TICK_TEXT, **overrides}


def _template() -> go.layout.Template:
    # Breathing room that automargin accounts for: invisible 7 px outside ticks
    # separate labels from the axis, and titles stand 14 px off the labels.
    # (ticklabelstandoff is not counted by automargin and clips long labels.)
    axis = dict(
        gridcolor=GRID,
        linecolor=AXIS,
        zeroline=False,
        ticks="outside",
        ticklen=7,
        tickcolor="rgba(0,0,0,0)",
        tickfont=dict(color=INK_MUTED, size=11.5),
        title=dict(font=dict(color=INK_SECONDARY, size=12.5), standoff=14),
        automargin=True,
    )
    return go.layout.Template(
        layout=dict(
            font=dict(family=FONT, color=INK, size=12),
            paper_bgcolor=SURFACE,
            plot_bgcolor=SURFACE,
            colorway=list(_HAZARD_SLOTS),
            margin=dict(l=24, r=28, t=32, b=24, pad=0),
            xaxis=axis,
            yaxis=axis,
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                font=dict(size=11, color=INK_SECONDARY), title=dict(text=""), bgcolor="rgba(0,0,0,0)",
            ),
            hoverlabel=dict(bgcolor=SURFACE, bordercolor=BORDER, font=dict(family=FONT, color=INK, size=12), align="left"),
            bargap=0.28,
            hovermode="closest",
            separators=".,",
        )
    )


pio.templates["pdc"] = _template()
PLOTLY_CONFIG = {"displaylogo": False, "scrollZoom": False, "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d", "toggleSpikelines"], "toImageButtonOptions": {"format": "png", "scale": 2}}


def apply_plotly_theme(fig: go.Figure, *, height: int = 360) -> go.Figure:
    # Backgrounds are set on the figure itself: Streamlit fills in its page
    # colour when a figure leaves them to the template.
    fig.update_layout(template="pdc", height=height, paper_bgcolor=SURFACE, plot_bgcolor=SURFACE)
    return fig


def empty_figure(message: str, *, height: int = 220) -> go.Figure:
    """A quiet placeholder with an explanation instead of an empty axis box."""

    fig = go.Figure()
    fig.add_annotation(text=message, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False, font=dict(color=INK_MUTED, size=13))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return apply_plotly_theme(fig, height=height)
