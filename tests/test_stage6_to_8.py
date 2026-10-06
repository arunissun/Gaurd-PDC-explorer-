"""Focused Stage 6–8 checks that do not profile or scan the local export."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from guard_pdc.analysis import build_analysis_frames, gender_message
from guard_pdc.api import ApiQueryResult, PartitionResult
from guard_pdc.config import MontandonConfig
from guard_pdc.models import QuerySpec
from guard_pdc.service import PdcEvidenceService
from guard_pdc.taxonomy import CATEGORY_ORDER
from guard_pdc.visuals import (
    MAP_NOTE,
    build_map_layers,
    country_choropleth_geojson,
    evidence_cards,
    figure_age_profile,
    figure_age_reconciliation,
    figure_category_availability,
    figure_distribution,
    figure_event_completeness,
    figure_event_duration,
    figure_exposure_panels,
    figure_geometry_quality,
    figure_monthly_events,
    figure_selected_category,
    figure_snapshot_counts,
    static_fallbacks,
    to_lonboard,
    to_pydeck,
    validate_footprint_geojson,
)


ROOT = Path(__file__).parents[1]


def fixture_json(name: str) -> dict:
    return json.loads((ROOT / "tests" / "fixtures" / name).read_text(encoding="utf-8"))


def fixture_jsonl(name: str) -> list[dict]:
    return [json.loads(line) for line in (ROOT / "tests" / "fixtures" / name).read_text(encoding="utf-8").splitlines() if line]


def api_result(collection: str, items: list[dict], query: QuerySpec) -> ApiQueryResult:
    partition = PartitionResult(
        collection=collection,
        start="2024-01-01T00:00:00Z",
        end="2024-02-01T00:00:00Z",
        request={"collections": [collection]},
        items=tuple(items),
        pages=(),
        returned_count=len(items),
        unique_count=len({item["id"] for item in items}),
        duplicate_ids=(),
        complete=True,
        stop_reason="pagination_exhausted",
        cache_key=f"stage6-{collection}",
        retrieved_at="2026-09-28T00:00:00Z",
        query_fingerprint=query.fingerprint,
    )
    return ApiQueryResult(collection, tuple(items), (partition,), len(items), partition.unique_count, (), True, query.fingerprint)


class FakeApiProvider:
    def __init__(self, results: dict[str, ApiQueryResult]):
        self.results = results

    def query_events(self, _query: QuerySpec, **_options) -> ApiQueryResult:
        return self.results["pdc-events"]

    def query_hazards(self, _query: QuerySpec, **_options) -> ApiQueryResult:
        return self.results["pdc-hazards"]

    def query_impacts(self, _query: QuerySpec, **_options) -> ApiQueryResult:
        return self.results["pdc-impacts"]


def fixture_result(*, categories: tuple[str, ...] = CATEGORY_ORDER, zero_child: bool = True):
    event = fixture_json("pdc_event.json")
    hazard = fixture_json("pdc_hazard.json")
    impacts = deepcopy(fixture_jsonl("pdc_impacts.jsonl"))
    if zero_child:
        impacts[1]["properties"]["monty:impact_detail"]["value"] = 0
    query = QuerySpec(
        analysis_mode="country_detail",
        country_code="PHL",
        year=2024,
        months=(1,),
        categories=categories,
        source_mode="api_only",
    )
    provider = FakeApiProvider(
        {
            "pdc-events": api_result("pdc-events", [event], query),
            "pdc-hazards": api_result("pdc-hazards", [hazard], query),
            "pdc-impacts": api_result("pdc-impacts", impacts, query),
        }
    )
    return PdcEvidenceService(MontandonConfig(api_token="fixture"), api_provider=provider).retrieve(query)


class AnalysisAndFigureTests(unittest.TestCase):
    def test_empty_country_result_retains_chart_schema(self) -> None:
        result = replace(fixture_result(), event_families=(), event_snapshots=(), hazard_snapshots=(), impact_observations=(), correlation_evidence=())
        frames = build_analysis_frames(result)
        self.assertIn("status", frames.category_status)
        self.assertIn("age_order", frames.demographics)
        for figure in (
            figure_event_completeness(frames), figure_selected_category(frames, "people"),
            figure_distribution(frames, "people"), figure_age_profile(frames, "missing"),
            figure_age_reconciliation(frames, "missing"),
        ):
            self.assertIsNotNone(figure)

    def test_frames_keep_grains_zero_missing_and_hazard_geometry(self) -> None:
        frames = build_analysis_frames(fixture_result())
        status = frames.category_status.set_index("impact_category")["status"]

        self.assertEqual(len(frames.event_families), 1)
        self.assertEqual(len(frames.event_snapshots), 1)
        self.assertEqual(len(frames.hazards), 1)
        self.assertEqual(len(frames.impacts), 2)
        self.assertEqual(status["people"], "present_positive")
        self.assertEqual(status["children_0_4"], "present_zero")
        self.assertEqual(status["households"], "missing")
        self.assertTrue(frames.geometry["cloned_coordinate"].all())
        self.assertIsNotNone(gender_message(frames))

        monthly = figure_monthly_events(frames)
        self.assertEqual(sum(sum(trace.y) for trace in monthly.data), 1)
        self.assertEqual(monthly.layout.meta["grain"], "one source event family")
        age = figure_age_profile(frames, frames.event_families.iloc[0]["family_key"])
        self.assertNotIn("People", tuple(age.data[0].y))

    def test_unrequested_category_is_not_labelled_missing(self) -> None:
        frames = build_analysis_frames(fixture_result(categories=("people",)))
        child = frames.category_status.set_index("impact_category").loc["children_0_4"]
        self.assertEqual(child["status"], "not_retrieved")

    def test_cards_and_static_fallbacks_are_bounded(self) -> None:
        frames = build_analysis_frames(fixture_result())
        self.assertEqual(evidence_cards(frames)["event_families"], 1)
        fallbacks = static_fallbacks(frames)
        self.assertEqual(set(fallbacks), {"monthly_events", "snapshot_counts", "category_availability", "event_completeness", "geometry_quality"})
        self.assertEqual(len(fallbacks["snapshot_counts"]), 1)

    def test_all_planned_stage6_figures_construct(self) -> None:
        frames = build_analysis_frames(fixture_result())
        family_key = frames.event_families.iloc[0]["family_key"]
        age_profile = figure_age_profile(frames, family_key)
        figures = [
            figure_monthly_events(frames),
            figure_snapshot_counts(frames),
            figure_category_availability(frames),
            figure_event_completeness(frames),
            figure_selected_category(frames, "people"),
            age_profile,
            figure_age_reconciliation(frames, family_key),
            *figure_exposure_panels(frames, family_key).values(),
            figure_distribution(frames, "people"),
            figure_event_duration(frames),
            figure_geometry_quality(frames),
        ]
        self.assertTrue(all(figure.layout.meta["grain"] for figure in figures))
        self.assertGreaterEqual(age_profile.layout.title.text.count("<br>"), 2)


class MapTests(unittest.TestCase):
    def test_map_data_deduplicates_event_family_and_coincident_impacts(self) -> None:
        frames = build_analysis_frames(fixture_result())
        family_key = frames.event_families.iloc[0]["family_key"]
        layers = build_map_layers(frames, selected_category="people", selected_family_key=family_key)

        self.assertEqual(len(layers.event_points), 1)
        self.assertEqual(len(layers.hazard_points), 1)
        self.assertEqual(len(layers.impact_points), 1)
        self.assertEqual(layers.disclaimer, MAP_NOTE)
        self.assertEqual(layers.render_mode, "individual")
        self.assertGreaterEqual(len(to_pydeck(layers).layers), 3)
        self.assertEqual(type(to_lonboard(layers)).__name__, "Column")

        boundaries = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"ISO_A3": "PHL"},
                    "geometry": {"type": "Polygon", "coordinates": [[[120.0, 14.0], [121.0, 14.0], [121.0, 15.0], [120.0, 14.0]]]},
                }
            ],
        }
        choropleth = country_choropleth_geojson(layers, boundaries)
        self.assertEqual(choropleth["features"][0]["properties"]["pdc_event_families"], 1)
        self.assertGreaterEqual(len(to_pydeck(layers, country_boundaries=boundaries).layers), 4)

    def test_footprints_require_valid_polygon_geojson(self) -> None:
        valid = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {"type": "Polygon", "coordinates": [[[120.0, 14.0], [121.0, 14.0], [121.0, 15.0], [120.0, 14.0]]]},
                }
            ],
        }
        self.assertEqual(validate_footprint_geojson(valid).status, "available")
        invalid = deepcopy(valid)
        invalid["features"][0]["geometry"]["coordinates"][0][0] = [220.0, 14.0]
        self.assertEqual(validate_footprint_geojson(invalid).status, "invalid")


if __name__ == "__main__":
    unittest.main()
