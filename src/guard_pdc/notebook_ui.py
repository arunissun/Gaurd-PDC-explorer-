"""Stage 8 thin interactive notebook controls over the shared package."""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterable
from dataclasses import replace
from html import escape
from typing import Any

from IPython.display import display
import ipywidgets as widgets
from manywidgets import Button, Column, Dropdown, Grid, RangeSlider, Row, Stat, Text, Toggle

from .analysis import AnalysisFrames, build_analysis_frames, gender_message, temporal_history, temporal_summary
from .exports import build_export_files, public_text
from .models import QueryResult, QuerySpec, parse_country_codes
from .service import country_group_counts, retrieve_country_group
from .taxonomy import CATEGORY_ORDER, HAZARD_OPTIONS, category_label
from .visuals import (
    build_map_layers,
    evidence_cards,
    figure_age_profile,
    figure_age_reconciliation,
    figure_category_availability,
    figure_distribution,
    figure_event_completeness,
    figure_event_duration,
    figure_change_timeline,
    figure_geometry_quality,
    figure_monthly_events,
    figure_selected_category,
    figure_snapshot_counts,
    figure_snapshot_timeline,
    to_lonboard,
)


RetrieveFunction = Callable[[QuerySpec], QueryResult]


class NotebookExplorer:
    """Stateful notebook view; only the retrieve button calls the provider."""

    def __init__(
        self,
        retrieve_fn: RetrieveFunction,
        *,
        country_options: Iterable[tuple[str, str]] = (("Philippines (PHL)", "PHL"),),
    ) -> None:
        self.retrieve_fn = retrieve_fn
        self.result: QueryResult | None = None
        self.results: dict[str, QueryResult] = {}
        self.frames: AnalysisFrames | None = None
        country_options = tuple(country_options)
        if not country_options:
            raise ValueError("country_options must contain at least one ISO3 option")

        self.analysis_mode = Dropdown(
            options=[["Country detail", "country_detail"], ["Annual country overview", "annual_country_overview"]],
            value="country_detail",
            label="Analysis view",
        )
        self.source_mode = Dropdown(
            options=[["Live Montandon API", "api_only"]],
            value="api_only",
            label="Data source",
        )
        self.country = widgets.Combobox(options=[value for _, value in country_options], value=country_options[0][1], description="Countries ISO3", placeholder="PHL, BGD, NPL", ensure_option=False, continuous_update=False, style={"description_width": "initial"})
        self.year = Dropdown(options=list(range(2000, 2027)), value=2024, label="Year")
        self.month_range = RangeSlider(low=1, high=12, min=1, max=12, step=1, label="Event-start months")
        self.specific_months = widgets.SelectMultiple(options=[(str(month), month) for month in range(1, 13)], description="Specific months")
        self.hazards = widgets.SelectMultiple(
            options=[(label, codes[0]) for label, codes in HAZARD_OPTIONS],
            description="Hazards",
        )
        self.impact_types = widgets.SelectMultiple(options=[("Affected total", "affected_total"), ("Cost", "cost")], value=("affected_total",), description="Impact types")
        self.categories = widgets.SelectMultiple(options=[(category_label(category), category) for category in CATEGORY_ORDER], value=("people",), description="Categories", rows=8)
        self.include_zero = Toggle(value=True, label="Include zero values")
        self.include_missing_geometry = Toggle(value=True, label="Keep missing geometry in tables")
        self.retrieve_button = Button(label="Retrieve evidence")
        self.progress = widgets.IntProgress(value=0, min=0, max=4, description="Progress")
        self.status = Text(value="Ready. Retrieve evidence queries the live Montandon API; local exports and the SQLite index are not used.", markdown=True)

        self.event_selector = Dropdown(options=[], value=None, label="Event")
        self.country_selector = Dropdown(options=[], value=None, label="View country")
        self.category_selector = Dropdown(
            options=[[category_label(category), category] for category in CATEGORY_ORDER],
            value="people",
            label="Display category",
        )
        self.log_scale = Toggle(value=False, label="Log display scale")
        self.export_button = widgets.Button(description="Prepare bounded downloads", button_style="primary")
        self.export_output = widgets.Output()
        self.outputs = [widgets.Output() for _ in range(8)]
        self.tabs = widgets.Tab(children=self.outputs)
        for index, title in enumerate(("Overview", "Events", "Exposure", "Demographics", "Timeline", "Map", "Quality", "Export")):
            self.tabs.set_title(index, title)
        self.advanced = widgets.Accordion(
            children=[widgets.VBox([self.specific_months])],
            titles=("Advanced retrieval",),
            selected_index=None,
        )

        controls = Grid(
            self.analysis_mode,
            self.source_mode,
            self.country,
            self.year,
            self.month_range,
            self.include_zero,
            self.include_missing_geometry,
            self.retrieve_button,
            columns=4,
            gap="12px",
        )
        selectors = Row(self.country_selector, self.event_selector, self.category_selector, self.log_scale, gap="12px")
        self.widget = Column(
            Text(value="## 1. Define analysis", markdown=True),
            controls,
            widgets.HBox([self.hazards, self.impact_types, self.categories]),
            self.advanced,
            Text(value="## 2. Retrieve evidence", markdown=True),
            Row(self.progress, self.status),
            Text(value="## 3–5. Review evidence, confidence, and export", markdown=True),
            selectors,
            self.tabs,
            gap="16px",
        )
        self.retrieve_button.on_click(lambda _: self.retrieve_now())
        self.event_selector.observe(self._view_changed, names="value")
        self.country_selector.observe(self._country_changed, names="value")
        self.category_selector.observe(self._view_changed, names="value")
        self.log_scale.observe(self._view_changed, names="value")
        self.analysis_mode.observe(self._mode_changed, names="value")
        self.export_button.on_click(self._prepare_exports)

    def display(self) -> None:
        display(self.widget)

    def _mode_changed(self, change: dict[str, Any]) -> None:
        annual = change["new"] == "annual_country_overview"
        self.country.description = "Countries ISO3 (optional)" if annual else "Countries ISO3"
        self.impact_types.disabled = annual
        self.categories.disabled = annual

    def _query(self) -> QuerySpec:
        months = tuple(self.specific_months.value) or tuple(range(int(self.month_range.low), int(self.month_range.high) + 1))
        annual = self.analysis_mode.value == "annual_country_overview"
        countries = parse_country_codes(self.country.value)
        hazard_codes = []
        selected = set(self.hazards.value)
        for _, codes in HAZARD_OPTIONS:
            if codes[0] in selected:
                hazard_codes.extend(codes)
        return QuerySpec(
            analysis_mode=self.analysis_mode.value,
            country_code=countries[0] if countries else None,
            year=int(self.year.value),
            months=months,
            hazard_codes=tuple(hazard_codes),
            impact_types=("affected_total",) if annual else tuple(self.impact_types.value),
            categories=("people",) if annual else tuple(self.categories.value),
            source_mode="api_only",
            include_zero_values=bool(self.include_zero.value),
            include_missing_geometry=bool(self.include_missing_geometry.value),
            refresh_api_cache=True,
        )

    def _queries(self) -> tuple[QuerySpec, ...]:
        countries = parse_country_codes(self.country.value)
        query = self._query()
        return tuple(replace(query, country_code=country) for country in countries) if countries else (query,)

    def _country_changed(self, change: dict[str, Any]) -> None:
        if change["new"] in self.results:
            self._select_country(change["new"])

    def _select_country(self, country: str) -> None:
        result = self.results[country]
        if self.result is result and self.frames is not None:
            return
        self.result, self.frames = result, build_analysis_frames(result)
        self.export_output.clear_output()
        self._set_event_options(self.frames)
        self._render()

    def retrieve_now(self) -> QueryResult | None:
        previous_result, previous_frames, previous_results = self.result, self.frames, self.results
        self.progress.value = 1
        self.status.value = "Validating the bounded query…"
        try:
            queries = self._queries()
            self.progress.value = 2
            self.status.value = "Retrieving event evidence…"
            results = retrieve_country_group(queries, self.retrieve_fn)
            self.progress.value = 3
            self.results = results
            country = next(iter(results))
            self.country_selector.options = [[code, code] for code in results]
            self.country_selector.value = country
            self._select_country(country)
            result = self.result
            assert result is not None
            self.progress.value = 4
            warning = f" Warnings: {'; '.join(public_text(value) for value in result.metadata.warnings)}" if result.metadata.warnings else ""
            counts = country_group_counts(results)
            self.status.value = f"Complete: {counts['countries']} country result(s), {counts['event_families']} distinct event families across the group; provider {result.metadata.provider}. View country switches charts and exports without a new request; country exposure values are not added together.{warning}"
            return result
        except Exception as error:  # notebook boundary: keep last good result visible
            self.result, self.frames, self.results = previous_result, previous_frames, previous_results
            self.progress.value = 0
            self.status.value = f"**Retrieval failed:** {type(error).__name__}: {public_text(str(error))}"
            return None

    def _set_event_options(self, frames: AnalysisFrames) -> None:
        options = [
            [f"{row.event_title or 'Untitled'} — {row.family_key}", row.family_key]
            for row in frames.event_families.itertuples()
        ]
        self.event_selector.options = options
        self.event_selector.value = options[0][1] if options else None

    def _view_changed(self, _change: dict[str, Any]) -> None:
        if self.frames is not None:
            self._render()

    def _render(self) -> None:
        assert self.frames is not None
        frames = self.frames
        family_key = self.event_selector.value
        category = self.category_selector.value

        with self.outputs[0]:
            self.outputs[0].clear_output(wait=True)
            cards = evidence_cards(frames)
            display(Grid(*(Stat(label=key.replace("_", " ").title(), value=value) for key, value in cards.items()), columns=3))
            display(figure_monthly_events(frames), figure_snapshot_counts(frames))
        with self.outputs[1]:
            self.outputs[1].clear_output(wait=True)
            display(figure_event_completeness(frames), frames.event_snapshots.head(100))
        with self.outputs[2]:
            self.outputs[2].clear_output(wait=True)
            display(
                figure_category_availability(frames),
                figure_selected_category(frames, category, log_scale=bool(self.log_scale.value)),
                figure_distribution(frames, category),
                frames.impacts.head(100),
            )
        with self.outputs[3]:
            self.outputs[3].clear_output(wait=True)
            if family_key:
                display(figure_age_profile(frames, family_key), figure_age_reconciliation(frames, family_key))
            message = gender_message(frames)
            if message:
                display(Text(value=message))
        with self.outputs[4]:
            self.outputs[4].clear_output(wait=True)
            display(figure_event_duration(frames))
            if family_key:
                history = temporal_history(frames, family_key, category)
                display(
                    figure_snapshot_timeline(frames, family_key),
                    figure_change_timeline(frames, family_key, category),
                    Text(value=temporal_summary(frames, family_key, category).message),
                    history.head(100),
                )
        with self.outputs[5]:
            self.outputs[5].clear_output(wait=True)
            layers = build_map_layers(frames, selected_category=category, selected_family_key=family_key)
            if layers.event_points.empty:
                display(Text(value="No valid event points are available for this view."))
            else:
                display(to_lonboard(layers))
            display(Text(value=layers.disclaimer))
        with self.outputs[6]:
            self.outputs[6].clear_output(wait=True)
            display(figure_geometry_quality(frames), frames.quality, frames.correlations.head(100), frames.provenance.head(100))
        with self.outputs[7]:
            self.outputs[7].clear_output(wait=True)
            display(
                Text(value="Downloads contain record IDs and evidence fingerprints, but no token, local path, raw pointer, or signed credential value."),
                self.export_button,
                self.export_output,
            )

    def _prepare_exports(self, _button: Any) -> None:
        if self.result is None:
            return
        files = build_export_files(
            self.result,
            selected_category=self.category_selector.value,
            selected_family_key=self.event_selector.value,
        )
        links = []
        for name, data in files.items():
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if name.endswith(".xlsx") else "application/octet-stream"
            encoded = base64.b64encode(data).decode("ascii")
            links.append(f'<li><a download="{escape(name)}" href="data:{mime};base64,{encoded}">{escape(name)}</a></li>')
        with self.export_output:
            self.export_output.clear_output(wait=True)
            display(widgets.HTML(value=f"<ul>{''.join(links)}</ul>"))


def create_notebook_app(
    retrieve_fn: RetrieveFunction | None = None,
    *,
    country_options: Iterable[tuple[str, str]] = (("Philippines (PHL)", "PHL"),),
) -> NotebookExplorer:
    if retrieve_fn is None:
        from .service import retrieve

        retrieve_fn = retrieve
    return NotebookExplorer(retrieve_fn, country_options=tuple(country_options))
