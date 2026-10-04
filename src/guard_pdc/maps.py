"""Hazard-aware maps: event overview, density, country choropleth and PDC alert areas.

Map rules (docs/VISUAL_SPEC.md):

* one marker per event family, coloured by hazard group, sized in five
  exposure classes; coincident points are never jittered;
* the PDC point is the event/hazard location, not an impact footprint;
* multi-country (regional) alerts are drawn faded as reference points, and
  tsunami bulletins (points at warning-centre locations) are hidden by default;
* a PDC "Maps" asset is a time series of PDC SmartAlert areas. It is shown
  only after it has been fetched, validated and labelled; nothing is invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import json
import math
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from pyproj import Geod

from .api import open_without_redirects
from .countries import country_name
from .figures import EVENT_HOVER, MEASURE_LABELS, _date, _hover_rows, _short
from .taxonomy import HAZARD_GROUP_ORDER
from .theme import (
    INK, INK_MUTED, PRIMARY, SEQUENTIAL, SIZE_LEGEND_VALUES, SURFACE, apply_plotly_theme, compact, empty_figure, exposure_size,
    hazard_color, sequential_scale, with_alpha,
)

MAP_NOTE = "PDC point represents the event/hazard location; it is not automatically an impact footprint."
ALERT_AREA_NOTE = (
    "Outline = PDC SmartAlert area published in the event's 'Maps' asset. It is the hazard alert area PDC used, "
    "not a verified impact footprint."
)
MAP_STYLE = "carto-positron"
GEOD = Geod(ellps="WGS84")
# Largest alert-area file seen in production probes: 630 KB (57 cyclone
# updates). The download cap is generous; drawing uses a simplified copy above
# DISPLAY_VERTEX_BUDGET vertices while areas use the full geometry.
MAX_FOOTPRINT_BYTES = 25_000_000
DISPLAY_VERTEX_BUDGET = 60_000

# Per-hazard reading notes for the selected-event map. Behaviour itself is
# data-driven (time slider whenever several dated alert areas exist; view fitted
# to the alert area), so these only explain what the outline means.
HAZARD_MAP_NOTES = {
    "Tropical cyclone": "PDC updates the cyclone alert area as the storm moves; use the slider to step through its updates.",
    "Earthquake & tsunami": "Earthquake alert areas surround the epicentre. Tsunami bulletin areas can cover a distant coast or a whole ocean basin.",
    "Flood": "Flood alert areas follow the affected basin or river reach.",
    "Landslide & avalanche": "Landslide alert areas are small and local; the view zooms in accordingly.",
    "Volcano": "Volcano alert areas are zones around the vent; several zones can be nested.",
    "Drought & extreme temperature": "Heat, cold and drought alerts are regional and often cross borders; the point is only a reference location.",
    "Storm & winter weather": "Storm and winter-weather alerts are regional; the point is only a reference location.",
    "Wildfire": "Wildfire alert areas surround the reported fire.",
}


def _fit(lons: np.ndarray, lats: np.ndarray, *, trim: bool) -> tuple[dict[str, float], float, np.ndarray]:
    """Centre and zoom covering the central 90% of points (all points when few)."""

    if trim and len(lons) >= 10:
        lon_low, lon_high = np.quantile(lons, [0.05, 0.95])
        lat_low, lat_high = np.quantile(lats, [0.05, 0.95])
    else:
        lon_low, lon_high, lat_low, lat_high = lons.min(), lons.max(), lats.min(), lats.max()
    pad_lon = max((lon_high - lon_low) * 0.12, 0.6)
    pad_lat = max((lat_high - lat_low) * 0.12, 0.6)
    lon_low, lon_high, lat_low, lat_high = lon_low - pad_lon, lon_high + pad_lon, lat_low - pad_lat, lat_high + pad_lat
    inside = (lons >= lon_low) & (lons <= lon_high) & (lats >= lat_low) & (lats <= lat_high)
    span = max(lon_high - lon_low, (lat_high - lat_low) * 1.6, 0.05)
    zoom = float(np.clip(math.log2(360 / span) + 0.6, 1, 11))
    return {"lon": float((lon_low + lon_high) / 2), "lat": float((lat_low + lat_high) / 2)}, zoom, inside


def _event_view(points: pd.DataFrame, show_all: bool) -> tuple[dict[str, float], float, np.ndarray]:
    """View for the event map and the mask of points inside it.

    Multi-country alerts often place their PDC point outside the selected
    country, so the default view is framed on single-country events (when at
    least three exist); "fit all" frames every shown event.
    """

    lons, lats = points["longitude"].to_numpy(float), points["latitude"].to_numpy(float)
    local = ~points["multi_country"].to_numpy(bool)
    if show_all or local.sum() < 3:
        return _fit(lons, lats, trim=not show_all)
    center, zoom, _ = _fit(lons[local], lats[local], trim=False)
    span_lon = 360 / 2 ** (zoom - 0.6)
    span_lat = span_lon / 1.6
    inside = (np.abs(lons - center["lon"]) <= span_lon / 2) & (np.abs(lats - center["lat"]) <= span_lat / 2)
    return center, zoom, inside


def _base_map(fig: go.Figure, center: Mapping[str, float], zoom: float, *, height: int) -> go.Figure:
    fig.update_layout(
        map=dict(style=MAP_STYLE, center=center, zoom=zoom),
        margin=dict(l=6, r=6, t=6, b=6),
        # Legend below the map so it never covers land or events.
        legend=dict(orientation="h", yanchor="top", y=-0.02, xanchor="left", x=0, bgcolor="rgba(0,0,0,0)", font=dict(size=11.5)),
    )
    return apply_plotly_theme(fig, height=height)


def map_notes(summary: pd.DataFrame, *, show_bulletins: bool = False, show_all: bool = False) -> dict[str, int]:
    """Counts behind the map's explanatory notes."""

    if summary.empty:
        return {"plotted": 0, "no_point": 0, "bulletins_hidden": 0, "outside_view": 0, "regional": 0}
    points = summary[summary["valid_point"]]
    bulletins = points["caveat"].fillna("").str.startswith("Tsunami bulletin")
    shown = points if show_bulletins else points[~bulletins]
    _, _, inside = _event_view(shown, show_all) if not shown.empty else (None, None, np.array([], dtype=bool))
    return {
        "plotted": int(len(shown)),
        "no_point": int((~summary["valid_point"]).sum()),
        "bulletins_hidden": int(bulletins.sum()) if not show_bulletins else 0,
        "outside_view": int((~inside).sum()) if len(inside) else 0,
        "regional": int(shown["multi_country"].sum()),
    }


def fig_event_map(
    summary: pd.DataFrame,
    measure: str = "people",
    *,
    layer: str = "points",
    show_bulletins: bool = False,
    show_all: bool = False,
    selected_family: str | None = None,
    height: int = 640,
) -> go.Figure:
    """One marker per event family; colour = hazard group, size = exposure class."""

    points = summary[summary["valid_point"]] if not summary.empty else summary
    if not show_bulletins and not points.empty:
        points = points[~points["caveat"].fillna("").str.startswith("Tsunami bulletin")]
    if points.empty:
        return empty_figure("No events with a valid PDC point in this view.", height=260)
    lons, lats = points["longitude"].to_numpy(float), points["latitude"].to_numpy(float)
    center, zoom, _ = _event_view(points, show_all)
    fig = go.Figure()
    if layer == "density":
        fig.add_trace(go.Densitymap(
            lat=lats, lon=lons, radius=18, colorscale=sequential_scale(transparent_start=True),
            showscale=False, hoverinfo="skip", opacity=0.85,
        ))
        return _base_map(fig, center, zoom, height=height)

    column = f"{measure}_peak"
    values = pd.to_numeric(points[column], errors="coerce")
    # One trace, largest markers first, so small events are drawn on top and
    # never hidden under a large neighbour. Size is log-scaled exposure.
    data = points.assign(_size=values.map(exposure_size), _value=values.fillna(-1)).sort_values(["_value"], ascending=False, kind="stable")
    colors = [
        with_alpha(hazard_color(group), 0.38 if regional else 0.9)
        for group, regional in zip(data["hazard_group"], data["multi_country"], strict=False)
    ]
    # A white halo under every marker separates overlapping points without jitter.
    fig.add_trace(go.Scattermap(lat=data["latitude"], lon=data["longitude"], mode="markers", marker=dict(size=data["_size"] + 2.5, color=SURFACE),
                                hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scattermap(
        lat=data["latitude"], lon=data["longitude"], mode="markers", marker=dict(size=data["_size"], color=colors),
        customdata=_hover_rows(data, measure), hovertemplate=EVENT_HOVER, showlegend=False, name="events",
    ))
    if selected_family is not None and selected_family in set(data["family_key"]):
        row = data[data["family_key"] == selected_family].iloc[0]
        fig.add_trace(go.Scattermap(lat=[row["latitude"]], lon=[row["longitude"]], mode="markers",
                                    marker=dict(size=row["_size"] + 14, color=INK, opacity=0.2), hoverinfo="skip", showlegend=False))
    # Legend: hazard colours, then reference sizes for the decades present.
    for group in [group for group in HAZARD_GROUP_ORDER if group in set(data["hazard_group"])]:
        fig.add_trace(go.Scattermap(lat=[None], lon=[None], mode="markers", marker=dict(size=10, color=hazard_color(group)), name=group,
                                    legendgroup="hazard", hoverinfo="skip"))
    positive = values[values > 0]
    if not positive.empty:
        low, high = positive.min(), positive.max()
        references = [value for value in SIZE_LEGEND_VALUES if low / 10 < value <= high * 3] or [SIZE_LEGEND_VALUES[0]]
        for index, value in enumerate(references):
            fig.add_trace(go.Scattermap(
                lat=[None], lon=[None], mode="markers", marker=dict(size=exposure_size(value), color="#9AA1AB"), name=compact(value),
                legendgroup="size", legendgrouptitle=dict(text=f"{MEASURE_LABELS[measure]} (peak)") if index == 0 else None, hoverinfo="skip",
            ))
    fig.update_layout(clickmode="event+select", legend=dict(groupclick="toggleitem", itemclick=False, itemdoubleclick=False))
    return _base_map(fig, center, zoom, height=height)


def fig_country_choropleth(counts: pd.DataFrame) -> go.Figure:
    """Distinct PDC source-event families per country (never summed exposure)."""

    if counts.empty:
        return empty_figure("No country codes in the event evidence.", height=260)
    values = counts["event_families"].astype(float)
    edges = np.unique(np.quantile(values, [0, 0.2, 0.4, 0.6, 0.8, 1.0]))
    bins = np.clip(np.searchsorted(edges, values, side="right") - 1, 0, max(len(edges) - 2, 0))
    steps = len(edges) - 1 or 1
    palette = [SEQUENTIAL[int(round(index * (len(SEQUENTIAL) - 2) / max(steps - 1, 1))) + 1] for index in range(steps)]
    scale = []
    for index, color in enumerate(palette):
        scale.extend([[index / steps, color], [(index + 1) / steps, color]])
    labels = [f"{int(edges[index])}–{int(edges[index + 1])}" for index in range(steps)] if len(edges) > 1 else [str(int(values.iloc[0]))]
    fig = go.Figure(go.Choropleth(
        locations=counts["country_code"], z=bins + 0.5, zmin=0, zmax=steps, locationmode="ISO-3", colorscale=scale,
        marker_line_color=SURFACE, marker_line_width=0.5,
        customdata=np.column_stack([counts["event_families"], counts.get("hazards", pd.Series([""] * len(counts))), counts["country_code"].map(country_name)]),
        hovertemplate="%{customdata[2]}: %{customdata[0]} distinct event families<br>%{customdata[1]}<extra></extra>",
        colorbar=dict(title=dict(text="Event families", font=dict(size=11)), tickvals=[index + 0.5 for index in range(steps)], ticktext=labels, thickness=12, len=0.6),
    ))
    fig.update_geos(projection_type="natural earth", showframe=False, showcoastlines=False, landcolor="#EEF0F3", showland=True, bgcolor="rgba(0,0,0,0)")
    return apply_plotly_theme(fig, height=460)


# ------------------------------------------------------------------ alert areas

@dataclass(frozen=True, slots=True)
class AlertArea:
    created: pd.Timestamp | None
    geometry: Mapping[str, Any]
    area_km2: float | None
    global_extent: bool
    vertices: int = 0


def _thin_ring(ring: list, step: int) -> list:
    """Keep every step-th vertex, the ring closure, and at least four points."""

    if step <= 1 or len(ring) <= 8:
        return ring
    thinned = ring[:-1:step]
    if len(thinned) < 4:
        thinned = ring[:-1][: max(3, len(ring) - 1)]
    return [*thinned, thinned[0]]


def display_geometry(geometry: Mapping[str, Any], step: int) -> Mapping[str, Any]:
    """A lighter copy of a polygon for drawing only (areas use the original)."""

    if step <= 1:
        return geometry
    if geometry.get("type") == "Polygon":
        return {"type": "Polygon", "coordinates": [_thin_ring(ring, step) for ring in geometry["coordinates"]]}
    if geometry.get("type") == "MultiPolygon":
        return {"type": "MultiPolygon", "coordinates": [[_thin_ring(ring, step) for ring in polygon] for polygon in geometry["coordinates"]]}
    return geometry


@dataclass(frozen=True, slots=True)
class AlertAreas:
    status: str
    message: str
    host: str | None = None
    areas: tuple[AlertArea, ...] = ()
    lines: tuple[Mapping[str, Any], ...] = ()
    bytes: int = 0
    point_inside: bool | None = None
    bbox: tuple[float, float, float, float] | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def latest(self) -> AlertArea | None:
        return self.areas[-1] if self.areas else None

    @property
    def display_step(self) -> int:
        """Vertex thinning step that keeps drawing within the display budget."""

        total = sum(area.vertices for area in self.areas)
        return max(1, math.ceil(total / DISPLAY_VERTEX_BUDGET))


def _polygons(geometry: Mapping[str, Any]) -> list:
    if geometry.get("type") == "Polygon":
        return [geometry.get("coordinates") or []]
    if geometry.get("type") == "MultiPolygon":
        return list(geometry.get("coordinates") or [])
    return []


def _ring_area(ring: list) -> float:
    lons = [point[0] for point in ring]
    lats = [point[1] for point in ring]
    area, _ = GEOD.polygon_area_perimeter(lons, lats)
    return abs(area) / 1e6


def _ring_contains(ring: list, lon: float, lat: float) -> bool:
    inside = False
    for index in range(len(ring)):
        x1, y1 = ring[index][:2]
        x2, y2 = ring[index - 1][:2]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-15) + x1:
            inside = not inside
    return inside


def _valid_position(point: Any) -> bool:
    return (
        isinstance(point, (list, tuple)) and len(point) >= 2
        and all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in point[:2])
        and -180 <= point[0] <= 180 and -90 <= point[1] <= 90
    )


def parse_alert_areas(value: Any, *, point: tuple[float, float] | None = None, max_features: int = 500, size: int = 0, host: str | None = None) -> AlertAreas:
    """Validate a PDC 'Maps' GeoJSON and order its SmartAlert areas by createDate."""

    if not isinstance(value, Mapping):
        return AlertAreas("invalid", "The PDC alert-area file is not a JSON object.", host)
    if value.get("type") == "FeatureCollection":
        features = value.get("features")
    elif value.get("type") == "Feature":
        features = [value]
    else:
        return AlertAreas("invalid", "The PDC alert-area file is not GeoJSON.", host)
    if not isinstance(features, list) or not features:
        return AlertAreas("invalid", "The PDC alert-area file has no features.", host)
    if len(features) > max_features:
        return AlertAreas("oversized", f"The PDC alert-area file has {len(features)} features (limit {max_features}).", host)
    areas, lines, positions = [], [], []
    for feature in features:
        geometry = feature.get("geometry") if isinstance(feature, Mapping) else None
        if not isinstance(geometry, Mapping):
            continue
        properties = feature.get("properties") if isinstance(feature.get("properties"), Mapping) else {}
        created = pd.to_datetime(properties.get("createDate"), errors="coerce", utc=True, format="mixed")
        created = None if pd.isna(created) else created
        if geometry.get("type") == "LineString":
            coordinates = geometry.get("coordinates") or []
            if coordinates and all(_valid_position(point) for point in coordinates):
                lines.append({"created": created, "geometry": geometry})
            continue
        polygons = _polygons(geometry)
        rings = [ring for polygon in polygons for ring in polygon]
        if not rings or not all(_valid_position(point) for ring in rings for point in ring):
            return AlertAreas("invalid", "An alert area contains invalid coordinates.", host)
        exterior_points = [point for polygon in polygons for point in polygon[0]]
        positions.extend(exterior_points)
        lon_span = max(point[0] for point in exterior_points) - min(point[0] for point in exterior_points)
        area = sum(_ring_area(polygon[0]) - sum(_ring_area(hole) for hole in polygon[1:]) for polygon in polygons)
        areas.append(AlertArea(created, geometry, None if lon_span > 180 else area, lon_span > 180, sum(len(ring) for ring in rings)))
    if not areas:
        return AlertAreas("invalid", "The PDC alert-area file contains no polygons.", host, lines=tuple(lines))
    areas.sort(key=lambda item: (item.created is None, item.created or pd.Timestamp(0, tz="UTC")))
    inside = None
    if point is not None and all(value is not None for value in point):
        latest = _polygons(areas[-1].geometry)
        inside = any(_ring_contains(polygon[0], point[0], point[1]) and not any(_ring_contains(hole, point[0], point[1]) for hole in polygon[1:]) for polygon in latest)
    bbox = (
        min(point[0] for point in positions), min(point[1] for point in positions),
        max(point[0] for point in positions), max(point[1] for point in positions),
    )
    notes = []
    if any(area.global_extent for area in areas):
        notes.append("At least one alert area spans more than half the globe (for example an ocean-basin tsunami bulletin); its area is not computed.")
    result = AlertAreas("available", f"{len(areas)} PDC alert-area version(s).", host, tuple(areas), tuple(lines), size, inside, bbox, tuple(notes))
    if result.display_step > 1:
        result = AlertAreas(
            result.status, result.message, host, result.areas, result.lines, size, inside, bbox,
            result.notes + (f"Outlines are simplified for drawing (every {result.display_step}th vertex); areas use the full PDC geometry.",),
        )
    return result


def fetch_alert_areas(
    url: str | None,
    *,
    allowed_hosts: tuple[str, ...],
    point: tuple[float, float] | None = None,
    opener: Callable[..., Any] = open_without_redirects,
    max_bytes: int = MAX_FOOTPRINT_BYTES,
) -> AlertAreas:
    """Fetch one event's PDC 'Maps' asset from its unsigned object URL.

    No credentials are sent, only allow-listed HTTPS hosts are contacted,
    redirects are refused (so an allowed host cannot forward the request
    elsewhere), the response is size-capped, and nothing is written to disk.
    """

    if not url:
        return AlertAreas("missing", "This event does not advertise a PDC alert-area asset.")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        return AlertAreas("not_allowed", "The alert-area host is not on the allow-list.", parsed.hostname)
    request = Request(url, headers={"Accept": "application/geo+json, application/json", "User-Agent": "guard-pdc-explorer/0.2"})
    try:
        with opener(request, timeout=45) as response:
            payload = response.read(max_bytes + 1)
    except HTTPError as error:
        if 300 <= error.code < 400:
            return AlertAreas("not_allowed", f"The alert-area host answered with a redirect (HTTP {error.code}); redirects are not followed.", parsed.hostname)
        status = "inaccessible" if error.code in {401, 403} else "failed"
        return AlertAreas(status, f"The alert-area request returned HTTP {error.code}.", parsed.hostname)
    except (TimeoutError, URLError, OSError, http.client.HTTPException) as error:
        return AlertAreas("failed", f"The alert-area request failed ({type(error).__name__}).", parsed.hostname)
    if len(payload) > max_bytes:
        return AlertAreas("oversized", f"The alert-area file exceeds {max_bytes // 1_000_000} MB.", parsed.hostname)
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return AlertAreas("invalid", "The alert-area file is not valid JSON.", parsed.hostname)
    return parse_alert_areas(value, point=point, size=len(payload), host=parsed.hostname)


def _layers(geometries: list[Mapping[str, Any]], color: str, *, fill_opacity: float, line_width: float = 1.5) -> list[dict[str, Any]]:
    collection = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": geometry} for geometry in geometries]}
    return [
        {"source": collection, "type": "fill", "color": color, "opacity": fill_opacity, "below": "traces"},
        {"source": collection, "type": "line", "color": color, "line": {"width": line_width}, "below": "traces"},
    ]


def fig_alert_area_map(row: Mapping[str, Any], footprint: AlertAreas, *, height: int = 460) -> go.Figure:
    """Selected event: latest PDC alert area, the PDC point, and a version slider."""

    color = hazard_color(str(row.get("hazard_group")))
    fig = go.Figure()
    if row.get("valid_point"):
        fig.add_trace(go.Scattermap(lat=[row["latitude"]], lon=[row["longitude"]], mode="markers", marker=dict(size=13, color=SURFACE), hoverinfo="skip", showlegend=False))
        fig.add_trace(go.Scattermap(lat=[row["latitude"]], lon=[row["longitude"]], mode="markers", marker=dict(size=9, color=INK), name="PDC event point",
                                    hovertemplate=f"PDC event point<br>{_short(row.get('title'))}<extra></extra>"))
    for line in footprint.lines:
        coordinates = line["geometry"]["coordinates"]
        fig.add_trace(go.Scattermap(lon=[point[0] for point in coordinates], lat=[point[1] for point in coordinates], mode="lines",
                                    line=dict(color=color, width=2), name="PDC track line", hoverinfo="skip", showlegend=False))
    if footprint.bbox is not None:
        lons = np.array([footprint.bbox[0], footprint.bbox[2]])
        lats = np.array([footprint.bbox[1], footprint.bbox[3]])
        center, zoom, _ = _fit(lons, lats, trim=False)
    elif row.get("valid_point"):
        center, zoom = {"lon": float(row["longitude"]), "lat": float(row["latitude"])}, 6.0
    else:
        return empty_figure("No PDC point or alert area to map.", height=240)
    areas = list(footprint.areas)
    if areas:
        step = footprint.display_step
        drawn = [display_geometry(area.geometry, step) for area in areas]

        def current(geometry: Mapping[str, Any]) -> dict[str, Any]:
            return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": geometry}]}

        # Layer 0: every version as one faint outline (drawn once). Layer 1: the
        # version shown, filled and outlined. Slider steps replace only layer 1's
        # source, so the figure carries each geometry about twice, not n² times.
        layers = [
            {"source": {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": geometry} for geometry in drawn]},
             "type": "line", "color": color, "opacity": 0.35, "line": {"width": 0.6}, "below": "traces"} if len(areas) > 1 else None,
            {"source": current(drawn[-1]), "type": "fill", "color": color, "opacity": 0.28, "fill": {"outlinecolor": color}, "below": "traces"},
        ]
        layers = [layer for layer in layers if layer is not None]
        fig.update_layout(map_layers=layers)
        if len(areas) > 1:
            steps = []
            for index, (area, geometry) in enumerate(zip(areas, drawn, strict=True)):
                label = area.created.strftime("%d %b %H:%M") if area.created is not None else f"v{index + 1}"
                if area.area_km2 is not None:
                    label = f"{label} · {compact(area.area_km2)} km²"
                steps.append({"label": label, "method": "relayout", "args": [{"map.layers[1].source": current(geometry)}]})
            fig.update_layout(sliders=[{
                "active": len(steps) - 1, "steps": steps, "x": 0.02, "len": 0.96, "y": 0, "yanchor": "top", "pad": {"t": 6, "b": 4},
                "currentvalue": {"prefix": "PDC alert area at ", "font": {"size": 12, "color": INK}},
                "font": {"size": 10, "color": "rgba(0,0,0,0)"},
                "tickcolor": "#C9CED6", "bgcolor": "#EEF0F3", "activebgcolor": PRIMARY, "bordercolor": "#E2E5EA",
            }])
    fig.update_layout(showlegend=False, margin=dict(l=0, r=0, t=0, b=60 if len(areas) > 1 else 0))
    fig = _base_map(fig, center, zoom, height=height)
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=70 if len(areas) > 1 else 0))
    return fig


def fig_alert_area_overlay(rows: list[tuple[Mapping[str, Any], AlertAreas]], *, height: int = 520) -> go.Figure:
    """Latest alert areas of several events of one hazard group, translucent so overlaps darken."""

    usable = [(row, footprint) for row, footprint in rows if footprint.latest is not None and not footprint.latest.global_extent]
    total_vertices = sum(footprint.latest.vertices for _, footprint in usable)
    step = max(1, math.ceil(total_vertices / DISPLAY_VERTEX_BUDGET))
    geometries, lons, lats = [], [], []
    for row, footprint in usable:
        latest = footprint.latest
        geometries.append(display_geometry(latest.geometry, step))
        if footprint.bbox is not None:
            lons.extend([footprint.bbox[0], footprint.bbox[2]])
            lats.extend([footprint.bbox[1], footprint.bbox[3]])
    if not geometries:
        return empty_figure("None of the selected events returned a usable alert area.", height=260)
    color = hazard_color(str(rows[0][0].get("hazard_group")))
    center, zoom, _ = _fit(np.array(lons), np.array(lats), trim=len(lons) >= 20)
    fig = go.Figure(go.Scattermap(
        lat=[row.get("latitude") for row, _ in rows], lon=[row.get("longitude") for row, _ in rows], mode="markers",
        marker=dict(size=7, color=INK, opacity=0.7),
        customdata=[[_short(row.get("title")), _date(row.get("event_date"))] for row, _ in rows],
        hovertemplate="%{customdata[0]}<br>%{customdata[1]}<extra></extra>", showlegend=False,
    ))
    fig.update_layout(map_layers=_layers(geometries, color, fill_opacity=0.12, line_width=0.6))
    return _base_map(fig, center, zoom, height=height)
