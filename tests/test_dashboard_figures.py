"""Dashboard figures, maps, event summary, country counts, diagnostics and report charts.

Synthetic event summaries keep every expectation exact; the fixture result runs
through the real service for the frame-based functions. No network access.
"""

from __future__ import annotations

import re
import unittest

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from guard_pdc import figures as F
from guard_pdc import maps as M
from guard_pdc.analysis import (
    SUMMARY_COLUMNS,
    build_analysis_frames,
    country_event_counts,
    combined_country_events,
    coverage_by_year,
    event_summary,
    exceedance,
)
from guard_pdc.config import MontandonConfig
from guard_pdc.diagnostics import (
    RAW_STRING_LIMIT,
    event_item_ids,
    example_search_bodies,
    partition_log,
    public_json,
    query_document,
    raw_items,
)
from guard_pdc.models import QuerySpec
from guard_pdc.report_charts import horizontal_bars, report_charts
from guard_pdc.service import PdcEvidenceService
from guard_pdc.taxonomy import HAZARD_GROUP_ORDER
from guard_pdc.theme import exposure_size
from tests.test_stage6_to_8 import FakeApiProvider, api_result, fixture_json, fixture_jsonl, fixture_result

MEASURES = ("people", "households", "schools", "hospitals", "capital")


def make_summary(*rows: dict) -> pd.DataFrame:
    """Event summary rows with every SUMMARY_COLUMNS column; overrides per row."""

    records = []
    for index, row in enumerate(rows):
        record = {column: None for column in SUMMARY_COLUMNS}
        record.update({
            "family_key": f"family-{index}", "title": f"Event {index}", "hazard_group": "Flood", "pdc_hazard_type": None,
            "countries": ("PHL",), "snapshot_count": 1, "valid_point": True, "longitude": 120.0 + index * 0.05, "latitude": 14.0,
            "caveat": None, "alert_level_max": None, "event_date": pd.Timestamp("2024-01-15", tz="UTC"),
        })
        for measure in MEASURES:
            record.update({f"{measure}_peak": np.nan, f"{measure}_latest": np.nan, f"{measure}_status": "missing", f"{measure}_changes": 0})
        record.update(row)
        record["n_countries"] = len(record["countries"])
        record["multi_country"] = record["n_countries"] > 1
        record["year"], record["month"] = record["event_date"].year, record["event_date"].month
        records.append(record)
    return pd.DataFrame.from_records(records, columns=list(SUMMARY_COLUMNS))


def day(month: int, year: int = 2024) -> pd.Timestamp:
    return pd.Timestamp(year=year, month=month, day=10, tz="UTC")


def tick_lines(label: str) -> list[str]:
    return re.sub(r"<span[^>]*>.*?</span>", "", label).split("<br>")


class WrapLabelTests(unittest.TestCase):
    def test_every_hazard_name_wraps_to_short_lines_without_splitting_words(self) -> None:
        for group in HAZARD_GROUP_ORDER:
            lines = F.wrap_label(group).split("<br>")
            self.assertEqual(" ".join(lines), group)
            for line in lines:
                self.assertTrue(len(line) <= F.LABEL_LINE_CHARS or " " not in line, (group, line))
                self.assertNotEqual(line, "&", group)

    def test_hazard_profile_ticks_are_wrapped_and_small_groups_get_a_range_line(self) -> None:
        rows = [{"hazard_group": "Drought & extreme temperature", "people_peak": value} for value in (1e3, 1e4, 1e5, 1e6, 1e7)]
        rows += [{"hazard_group": "Earthquake & tsunami", "people_peak": value} for value in (5e4, 6e4)]
        rows += [{"hazard_group": "Flood", "people_peak": 0.0}]
        fig = F.fig_hazard_profile(make_summary(*rows), "people")
        ticks = list(fig.layout.xaxis.categoryarray)
        self.assertEqual(len(ticks), 2)  # Flood has no positive value
        for label in ticks:
            self.assertTrue(all(len(line) <= F.LABEL_LINE_CHARS or " " not in line for line in tick_lines(label)), label)
        boxes = [trace for trace in fig.data if isinstance(trace, go.Box)]
        self.assertEqual(len(boxes), 1)  # 5 drought events -> box; 2 earthquakes -> range line only
        self.assertIn("2 events", ticks[1])
        self.assertTrue(fig.layout.xaxis.automargin)

    def test_hazard_profile_without_values_is_an_explained_placeholder(self) -> None:
        fig = F.fig_hazard_profile(make_summary({"people_peak": np.nan}), "people")
        self.assertIn("No event had", fig.layout.annotations[0].text)


class OverviewFigureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.summary = make_summary(
            {"event_date": day(1), "hazard_group": "Flood", "people_peak": 5e3},
            {"event_date": day(1), "hazard_group": "Flood", "people_peak": 2e6, "title": "Big flood"},
            {"event_date": day(3), "hazard_group": "Tropical cyclone", "people_peak": 5e5},
            {"event_date": day(3), "hazard_group": "Tropical cyclone", "people_peak": np.nan},
        )

    def stacked(self, fig: go.Figure) -> np.ndarray:
        traces = [trace for trace in fig.data if getattr(trace, "stackgroup", None)]
        return np.nansum(np.array([np.array(trace.y, dtype=float) for trace in traces]), axis=0)

    def test_monthly_stack_counts_distinct_events_by_size_and_by_hazard(self) -> None:
        months = (1, 2, 3)
        by_size = F.fig_impact_timeseries(self.summary, "people", (2024,), months, split="size")
        by_hazard = F.fig_impact_timeseries(self.summary, "people", (2024,), months, split="hazard")
        for fig in (by_size, by_hazard):
            totals = self.stacked(fig)
            self.assertEqual(list(totals[:3]), [2.0, 0.0, 2.0])
            self.assertTrue(all(np.isnan(np.array(trace.y, dtype=float)[3:]).all() for trace in fig.data if getattr(trace, "stackgroup", None)))
        self.assertEqual({trace.name for trace in by_hazard.data if trace.stackgroup}, {"Flood", "Tropical cyclone"})
        self.assertIn(F.NO_VALUE_LABEL, {trace.name for trace in by_size.data if trace.stackgroup})
        hover = [trace for trace in by_size.data if not trace.stackgroup][0]
        self.assertIn("Big flood", hover.customdata[0])  # the month's largest event, not a sum

    def test_seasonality_cells_count_distinct_events_and_mark_unrequested_months(self) -> None:
        fig = F.fig_seasonality(self.summary, (2024,), (1, 2, 3))
        row = fig.data[0].z[0]
        self.assertEqual(list(row[:3]), [2, 0, 2])
        self.assertTrue(all(value is None for value in row[3:]))

    def test_measure_timeseries_shows_the_largest_event_not_a_sum(self) -> None:
        fig = F.fig_measure_timeseries(self.summary, "people", (2024,), (1, 2, 3))
        self.assertEqual(fig.data[0].y[:3], (2e6, 0.0, 5e5))


class ExposureFigureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.summary = make_summary(
            *[{"hazard_group": "Flood", "people_peak": value, "schools_peak": schools} for value, schools in ((1e3, 0), (1e4, 3), (1e4, 12), (1e6, None))],
            {"hazard_group": "Volcano", "people_peak": 0.0},
        )

    def test_exceedance_counts_events_at_or_above_each_threshold_including_ties(self) -> None:
        curve = exceedance(self.summary, "people").set_index("threshold")["events"]
        self.assertEqual(curve.to_dict(), {1e6: 1, 1e4: 3, 1e3: 4})  # zero excluded; two events tie at 10k
        self.assertEqual(len(F.fig_exceedance(self.summary, "people").data), 1)

    def test_distribution_histogram_and_ecdf_cover_every_positive_event(self) -> None:
        histogram = F.fig_distribution(self.summary, "people", "histogram")
        self.assertEqual(sum(histogram.data[0].y), 4)
        ecdf = F.fig_distribution(self.summary, "people", "ecdf")
        self.assertEqual(ecdf.data[0].y[-1], 1.0)

    def test_infrastructure_stats_keep_zero_missing_and_positive_apart(self) -> None:
        self.assertEqual(F.infrastructure_stats(self.summary, "schools"), {"with_value": 3, "with_any": 2, "zero": 1})
        self.assertEqual(len(F.fig_infrastructure(self.summary, "schools").data[0].x), 2)

    def test_top_events_are_ordered_and_timeline_sizes_follow_the_map_scale(self) -> None:
        top = F.fig_top_events(self.summary, "people", n=3)
        values = [value for trace in top.data for value in trace.x]
        self.assertEqual(sorted(values, reverse=True), [1e6, 1e4, 1e4])
        timeline = F.fig_timeline(self.summary, "people")
        sizes = [size for trace in timeline.data for size in trace.marker.size]
        self.assertEqual(sorted(sizes), sorted(exposure_size(value) for value in self.summary["people_peak"]))

    def test_size_classes_have_inclusive_lower_edges(self) -> None:
        classes = F.size_class(pd.Series([9_999, 10_000, 1_000_000, 0, None]), "people")
        labels = F.size_class_labels("people")
        self.assertEqual(list(classes[:3]), [labels[0], labels[1], labels[3]])


class CountryCountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.summary = make_summary(
            {"countries": ("PHL",), "hazard_group": "Flood", "snapshot_count": 2},
            {"countries": ("PHL", "VNM"), "hazard_group": "Tropical cyclone", "snapshot_count": 3},
            {"countries": ("VNM",), "hazard_group": "Flood"},
            {"countries": (), "hazard_group": "Flood"},
        )

    def test_multi_country_events_count_once_in_each_country_and_no_country_is_excluded(self) -> None:
        counts = country_event_counts(self.summary).set_index("country_code")
        self.assertEqual(counts["event_families"].to_dict(), {"PHL": 2, "VNM": 2})
        self.assertEqual(counts["multi_country_events"].to_dict(), {"PHL": 1, "VNM": 1})
        self.assertEqual(counts.loc["PHL", "event_snapshots"], 5)
        self.assertEqual(counts.loc["PHL", "top_hazards"], "Flood (1), Tropical cyclone (1)")
        # Country totals overlap: their sum (4) exceeds the distinct events with a country (3).
        self.assertGreater(counts["event_families"].sum(), self.summary["n_countries"].gt(0).sum())

    def test_colour_bar_scale_is_round(self) -> None:
        self.assertEqual(M.nice_scale(358), (400, 100))
        self.assertEqual(M.nice_scale(59), (60, 20))
        self.assertEqual(M.nice_scale(3), (3.0, 1.0))
        self.assertEqual(M.nice_scale(0), (1.0, 1.0))

    def test_combined_events_appear_once_and_carry_no_exposure_values(self) -> None:
        phl = make_summary({"family_key": "shared", "countries": ("PHL", "VNM"), "people_peak": 9e5},
                           {"family_key": "phl-only", "people_peak": 1e4})
        vnm = make_summary({"family_key": "shared", "countries": ("PHL", "VNM"), "people_peak": 2e5})
        combined = combined_country_events({"PHL": phl, "VNM": vnm})
        self.assertEqual(sorted(combined["family_key"]), ["phl-only", "shared"])
        # The shared event has a PHL value and a VNM value: neither is kept.
        self.assertTrue(combined["people_peak"].isna().all())
        self.assertEqual(set(combined["people_status"]), {"not_combined"})
        self.assertEqual(list(combined.columns), list(SUMMARY_COLUMNS))
        self.assertTrue(combined_country_events({"PHL": phl.iloc[0:0]}).empty)

    def test_uniform_size_map_ignores_exposure_values(self) -> None:
        fig = M.fig_event_map(self.summary, "people", uniform_size=9)
        self.assertEqual(set(fig.data[1].marker.size), {9.0})

    def test_choropleth_carries_iso3_for_selection_and_handles_no_countries(self) -> None:
        fig = M.fig_country_choropleth(country_event_counts(self.summary), period="2024 · Jan–Dec", fit=True)
        self.assertEqual(fig.layout.geo.fitbounds, "locations")
        self.assertEqual(sorted(row[0] for row in fig.data[0].customdata), ["PHL", "VNM"])
        self.assertIn("2024 · Jan–Dec", fig.data[0].hovertemplate)
        self.assertEqual(fig.layout.clickmode, "event+select")
        # A handful of countries: continuous scale from zero with round, evenly spaced ticks.
        self.assertEqual((fig.data[0].zmin, fig.data[0].zmax), (0.0, 2.0))
        self.assertEqual(fig.data[0].colorbar.tickmode, "linear")
        self.assertEqual(list(fig.data[0].z), [2.0, 2.0])
        empty = M.fig_country_choropleth(country_event_counts(make_summary({"countries": ()})))
        self.assertIn("No country codes", empty.layout.annotations[0].text)


class EventMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.summary = make_summary(
            {"people_peak": 1e6, "family_key": "big"},
            {"people_peak": 1e3, "countries": ("PHL", "VNM")},
            {"people_peak": 5e4, "caveat": "Tsunami bulletin for a warning region"},
            {"people_peak": 2e3, "valid_point": False, "longitude": None, "latitude": None},
        )

    def test_notes_count_plotted_hidden_and_regional_events(self) -> None:
        notes = M.map_notes(self.summary)
        self.assertEqual(notes["plotted"], 2)
        self.assertEqual(notes["no_point"], 1)
        self.assertEqual(notes["bulletins_hidden"], 1)
        self.assertEqual(notes["regional"], 1)

    def test_points_layer_draws_halo_and_largest_first_and_highlights_selection(self) -> None:
        fig = M.fig_event_map(self.summary, "people", selected_family="big")
        halo, events = fig.data[0], fig.data[1]
        self.assertEqual(len(events.lat), 2)
        self.assertEqual(list(events.marker.size), [exposure_size(1e6), exposure_size(1e3)])
        self.assertEqual(list(halo.marker.size), [size + 2.5 for size in events.marker.size])
        self.assertIn("0.38", events.marker.color[1])  # multi-country alert drawn faded
        self.assertTrue(any(trace.marker.opacity == 0.2 for trace in fig.data[2:]))

    def test_cluster_layer_counts_events_without_a_halo(self) -> None:
        fig = M.fig_event_map(self.summary, "people", layer="clusters")
        events = fig.data[0]
        self.assertTrue(events.cluster.enabled)
        self.assertEqual(events.cluster.maxzoom, M.CLUSTER_MAX_ZOOM)
        self.assertFalse(any(trace.cluster.enabled for trace in fig.data[1:] if trace.cluster is not None and trace.cluster.enabled is not None))

    def test_density_and_empty_maps(self) -> None:
        self.assertIsInstance(M.fig_event_map(self.summary, "people", layer="density").data[0], go.Densitymap)
        empty = M.fig_event_map(make_summary({"valid_point": False}), "people")
        self.assertIn("No events with a valid PDC point", empty.layout.annotations[0].text)


def square(lon: float, lat: float, size: float) -> list:
    return [[lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size], [lon, lat]]


class AlertAreaTests(unittest.TestCase):
    def collection(self) -> dict:
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"createDate": "2024-01-02T00:00:00Z"}, "geometry": {"type": "Polygon", "coordinates": [square(120, 14, 1.0)]}},
            {"type": "Feature", "properties": {"createDate": "2024-01-01T00:00:00Z"}, "geometry": {"type": "Polygon", "coordinates": [square(120, 14, 0.5)]}},
            {"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[120, 14], [121, 15]]}},
        ]}

    def test_versions_are_ordered_by_creation_and_the_point_is_tested_against_the_latest(self) -> None:
        areas = M.parse_alert_areas(self.collection(), point=(120.8, 14.8))
        self.assertEqual(areas.status, "available")
        self.assertEqual(len(areas.areas), 2)
        self.assertEqual(len(areas.lines), 1)
        self.assertGreater(areas.latest.area_km2, areas.areas[0].area_km2)
        self.assertTrue(areas.point_inside)
        self.assertEqual(areas.bbox, (120, 14, 121.0, 15.0))
        self.assertFalse(M.parse_alert_areas(self.collection(), point=(125.0, 14.5)).point_inside)

    def test_invalid_files_are_reported_never_repaired(self) -> None:
        self.assertEqual(M.parse_alert_areas([]).status, "invalid")
        self.assertEqual(M.parse_alert_areas({"type": "FeatureCollection", "features": []}).status, "invalid")
        bad = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[500, 14], [121, 14], [121, 15], [500, 14]]]}}
        self.assertEqual(M.parse_alert_areas(bad).status, "invalid")
        self.assertEqual(M.parse_alert_areas(self.collection(), max_features=2).status, "oversized")

    def test_display_thinning_keeps_ring_closure_and_area_uses_full_geometry(self) -> None:
        ring = [[120 + index * 0.01, 14 + (index % 2) * 0.01] for index in range(40)] + [[120, 14]]
        thinned = M.display_geometry({"type": "Polygon", "coordinates": [ring]}, 5)["coordinates"][0]
        self.assertLess(len(thinned), len(ring))
        self.assertGreaterEqual(len(thinned), 4)
        self.assertEqual(thinned[0], thinned[-1])
        self.assertEqual(M.display_geometry({"type": "Point", "coordinates": [1, 2]}, 5)["type"], "Point")

    def test_alert_area_map_draws_outline_or_falls_back_to_the_point(self) -> None:
        row = make_summary({"hazard_group": "Flood"}).iloc[0]
        available = M.fig_alert_area_map(row, M.parse_alert_areas(self.collection(), point=(120.8, 14.8)))
        self.assertTrue(available.layout.map.layers)
        missing = M.fig_alert_area_map(row, M.AlertAreas("inaccessible", "HTTP 403"))
        self.assertFalse(missing.layout.map.layers)
        self.assertEqual(len(missing.data), 2)  # halo + PDC point only; nothing invented


class FrameFunctionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = fixture_result()
        cls.frames = build_analysis_frames(cls.result)

    def test_event_summary_has_one_row_per_family_and_distinct_statuses(self) -> None:
        summary = event_summary(self.frames)
        self.assertEqual(list(summary.columns), list(SUMMARY_COLUMNS))
        self.assertEqual(len(summary), len(self.result.event_families))
        self.assertEqual(summary["family_key"].nunique(), len(summary))
        statuses = {"present_positive", "present_zero", "missing", "unavailable", "conflicting", "not_retrieved"}
        for measure in MEASURES:
            self.assertTrue(set(summary[f"{measure}_status"]) <= statuses)

    def test_coverage_reindexes_every_requested_year(self) -> None:
        coverage = coverage_by_year(self.frames)
        self.assertEqual(list(coverage["year"]), list(self.result.query.years))
        self.assertEqual(int(coverage["event_families"].sum()), len(self.result.event_families))

    def test_raw_items_return_the_original_items_of_one_event(self) -> None:
        family = self.frames.event_families["family_key"].iloc[0]
        ids = event_item_ids(self.frames, family)
        self.assertTrue(ids["pdc-events"])
        items = raw_items(self.result, ids)
        self.assertEqual([item["id"] for item in items["pdc-events"]], ids["pdc-events"])
        self.assertIn("properties", items["pdc-events"][0])
        self.assertEqual(raw_items(self.result, {"pdc-events": ["not-an-id"]}), {"pdc-events": []})

    def test_partition_log_lists_every_window_with_pages(self) -> None:
        log = partition_log({"PHL": self.result})
        self.assertEqual(set(log["collection"]), set(self.result.retrieval_state))
        self.assertEqual(int(log["items"].sum()), sum(api.unique_count for api in self.result.retrieval_state.values()))
        self.assertTrue(log["complete"].all())
        self.assertNotIn("authorization", " ".join(log.columns).lower())


class DiagnosticsTests(unittest.TestCase):
    def test_query_document_and_search_bodies_follow_the_query_mode(self) -> None:
        detail = QuerySpec(analysis_mode="country_detail", country_code="PHL", year=2024, months=(2, 3), source_mode="api_only")
        document = query_document(detail)
        self.assertEqual(document["query_fingerprint"], detail.fingerprint)
        self.assertEqual((document["retrieval_windows"], document["first_window"], document["last_window"]), (2, "2024-02", "2024-03"))
        bodies = example_search_bodies(detail)
        self.assertEqual(set(bodies), {"pdc-events", "pdc-hazards", "pdc-impacts"})
        self.assertIn("2024-02-01T00:00:00Z", str(bodies["pdc-events"]))
        world = QuerySpec(analysis_mode="annual_country_overview", country_code=None, year=2023, months=(1,), source_mode="api_only",
                          impact_types=("affected_total",), categories=("people",))
        self.assertEqual(set(example_search_bodies(world)), {"pdc-events", "pdc-hazards"})
        self.assertNotIn("PHL", str(example_search_bodies(world)))

    def test_public_json_redacts_credentials_and_bounds_long_strings(self) -> None:
        value = {"href": "https://host.example/a.json?X-Amz-Signature=abc&x=1", "nested": ["Bearer secret-token", "C:\\Users\\me\\file"], "n": 3,
                 "long": "x" * (RAW_STRING_LIMIT + 10)}
        public = public_json(value)
        text = str(public)
        self.assertNotIn("abc", text)
        self.assertNotIn("secret-token", text)
        self.assertNotIn("Users", text)
        self.assertEqual(public["n"], 3)
        self.assertTrue(public["long"].endswith(f"({RAW_STRING_LIMIT + 10:,} characters)"))


class ReportChartTests(unittest.TestCase):
    def test_country_detail_charts_include_largest_events_and_escape_titles(self) -> None:
        summary = make_summary({"people_peak": 5e5, "title": "Flood <Luzon> & more", "event_date": day(2)}, {"people_peak": 1e3, "event_date": day(2)})
        query = QuerySpec(analysis_mode="country_detail", country_code="PHL", year=2024, months=(1, 2), source_mode="api_only")
        charts = report_charts(summary, query, "people")
        self.assertEqual(set(charts), {"chart_events_by_month.svg", "chart_events_by_hazard.svg", "chart_top_events.svg"})
        svg = charts["chart_top_events.svg"][1]
        self.assertIn("Flood &lt;Luzon&gt; &amp; more", svg)
        self.assertNotIn("<Luzon>", svg)
        self.assertIn("500k", svg)

    def test_annual_overview_charts_rank_countries_by_distinct_events(self) -> None:
        summary = make_summary({"countries": ("PHL", "VNM")}, {"countries": ("VNM",)})
        query = QuerySpec(analysis_mode="annual_country_overview", country_code=None, year=2024, months=(1,), source_mode="api_only",
                          impact_types=("affected_total",), categories=("people",))
        charts = report_charts(summary, query)
        self.assertIn("chart_countries.svg", charts)
        self.assertNotIn("chart_top_events.svg", charts)
        svg = charts["chart_countries.svg"][1]
        self.assertLess(svg.index("Vietnam"), svg.index("Philippines"))
        self.assertEqual(report_charts(summary.iloc[0:0], query), {})

    def test_bars_are_proportional(self) -> None:
        svg = horizontal_bars("t", ["a", "b"], [10.0, 5.0])
        widths = [float(width) for width in re.findall(r"<rect x='250' y='[^']+' width='([^']+)'", svg)]
        self.assertAlmostEqual(widths[0], 2 * widths[1], places=1)


class AnnualServiceTests(unittest.TestCase):
    def test_annual_overview_retrieves_no_impacts_and_counts_countries(self) -> None:
        spec = QuerySpec(analysis_mode="annual_country_overview", country_code=None, year=2024, months=(1,), source_mode="api_only",
                         impact_types=("affected_total",), categories=("people",))
        fake = FakeApiProvider({
            "pdc-events": api_result("pdc-events", [fixture_json("pdc_event.json")], spec),
            "pdc-hazards": api_result("pdc-hazards", [fixture_json("pdc_hazard.json")], spec),
            "pdc-impacts": api_result("pdc-impacts", fixture_jsonl("pdc_impacts.jsonl"), spec),
        })
        result = PdcEvidenceService(MontandonConfig(api_token="fixture"), api_provider=fake).retrieve(spec)
        self.assertNotIn("pdc-impacts", result.retrieval_state)
        summary = event_summary(build_analysis_frames(result))
        self.assertTrue((summary["people_status"] == "not_retrieved").all())
        counts = country_event_counts(summary)
        self.assertEqual(int(counts["event_families"].max()), 1)


if __name__ == "__main__":
    unittest.main()
