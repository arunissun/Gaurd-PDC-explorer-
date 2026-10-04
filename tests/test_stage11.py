"""Focused Stage 11 temporal checks; no profiler or local-data scan."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import unittest

from guard_pdc.analysis import build_analysis_frames, temporal_history, temporal_summary
from guard_pdc.config import MontandonConfig
from guard_pdc.exports import build_export_files
from guard_pdc.models import QuerySpec
from guard_pdc.service import PdcEvidenceService, derive_pdc_exposure_snapshot_time
from guard_pdc.visuals import figure_change_timeline, figure_snapshot_timeline
from tests.test_stage6_to_8 import FakeApiProvider, api_result, fixture_json, fixture_jsonl


def temporal_result(values: tuple[int, ...] = (100, 100, 150)):
    uuid = "11111111-1111-1111-1111-111111111111"
    epochs = (1705276800, 1705363200, 1705449600)
    events, hazards, impacts = [], [], []
    event_template = fixture_json("pdc_event.json")
    hazard_template = fixture_json("pdc_hazard.json")
    impact_template = fixture_jsonl("pdc_impacts.jsonl")[0]
    for epoch, value in zip(epochs, values, strict=True):
        event = deepcopy(event_template)
        hazard = deepcopy(hazard_template)
        impact = deepcopy(impact_template)
        event_id = f"pdc-event-{uuid}-pdc-source-fixture-001-{epoch}"
        hazard_id = f"pdc-hazard-{uuid}-pdc-source-fixture-001-{epoch}"
        impact_id = f"pdc-impact-{uuid}-pdc-source-fixture-001-{epoch}-1-population-total-PHL"
        event["id"], hazard["id"], impact["id"] = event_id, hazard_id, impact_id
        event["properties"]["processing:version"] = "0.2.0"
        hazard["properties"]["processing:version"] = "0.2.0"
        impact["properties"]["processing:version"] = "0.2.0"
        impact["properties"]["monty:impact_detail"]["value"] = value
        event["links"] = [
            {"rel": "related", "href": f"../pdc-hazards/{hazard_id}.json", "collection": "pdc-hazards"},
            {"rel": "related", "href": f"../pdc-impacts/{impact_id}.json", "collection": "pdc-impacts"},
        ]
        hazard["links"] = [{"rel": "related", "href": f"../pdc-events/{event_id}.json", "collection": "pdc-events"}]
        impact["links"] = [{"rel": "related", "href": f"../pdc-events/{event_id}.json", "collection": "pdc-events"}]
        events.append(event)
        hazards.append(hazard)
        impacts.append(impact)
    query = QuerySpec(
        analysis_mode="country_detail",
        country_code="PHL",
        year=2024,
        months=(1,),
        categories=("people",),
        source_mode="api_only",
    )
    provider = FakeApiProvider(
        {
            "pdc-events": api_result("pdc-events", events, query),
            "pdc-hazards": api_result("pdc-hazards", hazards, query),
            "pdc-impacts": api_result("pdc-impacts", impacts, query),
        }
    )
    return PdcEvidenceService(MontandonConfig(api_token="fixture"), api_provider=provider).retrieve(query)


class TemporalTests(unittest.TestCase):
    def test_item_id_time_is_derived_and_repeated_snapshots_are_retained(self) -> None:
        result = temporal_result()
        frames = build_analysis_frames(result)
        family_key = result.event_families[0].family_key
        history = temporal_history(frames, family_key, "people")

        self.assertEqual(len(frames.event_snapshots), 3)
        self.assertEqual(len(frames.impacts), 3)
        self.assertEqual(history["exposure_snapshot_time"].nunique(), 3)
        self.assertEqual(history["is_change_point"].sum(), 2)
        self.assertTrue(history["snapshot_time_method"].eq("pdc_item_id_exposure_timestamp").all())
        self.assertEqual(temporal_summary(frames, family_key, "people").status, "increased")
        self.assertEqual(derive_pdc_exposure_snapshot_time("pdc-event-fixture-001"), (None, None))
        valid_id = result.event_snapshots[0].event_item_id
        for version in (None, "0.1.1", "0.2.0", "0.2.7", "v0.2.7"):
            with self.subTest(version=version):
                self.assertIsNotNone(derive_pdc_exposure_snapshot_time(valid_id, version)[0])
        self.assertEqual(derive_pdc_exposure_snapshot_time(valid_id, "0.3.0"), (None, None))
        self.assertEqual(
            derive_pdc_exposure_snapshot_time("pdc-event-not-a-uuid-source-1705276800", "0.2.0"),
            (None, None),
        )

        snapshot_figure = figure_snapshot_timeline(frames, family_key)
        change_figure = figure_change_timeline(frames, family_key, "people")
        self.assertEqual(len(snapshot_figure.data[0].x), 3)
        self.assertTrue(any(trace.name == "Event datetime" for trace in snapshot_figure.data))
        self.assertEqual(len(change_figure.data[0].x), 2)
        self.assertIn("Exposure snapshot time", change_figure.layout.xaxis.title.text)
        self.assertIn("PDC exposure snapshot value", change_figure.layout.title.text)
        self.assertIn("not observed impact-report", change_figure.layout.title.text)

        files = build_export_files(result, selected_category="people", selected_family_key=family_key)
        report = files["report.md"].decode("utf-8")
        self.assertIn("## Temporal evolution", report)
        self.assertIn("exposure increased", report)
        self.assertIn("not observed impact-report", report)

    def test_temporal_summary_uses_all_supported_non_forecast_states(self) -> None:
        frames = build_analysis_frames(temporal_result())
        family_key = frames.event_families.iloc[0]["family_key"]

        def status(values: tuple[int, ...]) -> str:
            impacts = frames.impacts.copy()
            impacts["numeric_value"] = values
            impacts["original_value"] = values
            return temporal_summary(replace(frames, impacts=impacts), family_key, "people").status

        self.assertEqual(status((100, 100, 100)), "stable")
        self.assertEqual(status((100, 150, 100)), "changed")
        self.assertEqual(status((150, 100, 90)), "decreased")
        self.assertEqual(status((100, 100, 150)), "increased")
        self.assertEqual(temporal_summary(replace(frames, impacts=frames.impacts.iloc[:1]), family_key, "people").status, "insufficient")


if __name__ == "__main__":
    unittest.main()
