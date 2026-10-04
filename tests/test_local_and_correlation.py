"""Local profiling, index, routing, merge, and correlation checks."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from guard_pdc.api import ApiQueryResult, PartitionResult
from guard_pdc.config import MontandonConfig
from guard_pdc.local import PdcLocalProvider
import guard_pdc.local as local_module
from guard_pdc.models import QuerySpec
from guard_pdc.service import PdcEvidenceService


ROOT = Path(__file__).parents[1]


def fixture_json(name: str) -> dict:
    return json.loads((ROOT / "tests" / "fixtures" / name).read_text(encoding="utf-8"))


def fixture_jsonl(name: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (ROOT / "tests" / "fixtures" / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_export(path: Path, items: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in items) + "\n",
        encoding="utf-8",
    )


def local_provider(root: Path, export: Path) -> PdcLocalProvider:
    return PdcLocalProvider(export, root / "index.sqlite", root / "coverage.json")


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
        cache_key=f"fixture-{collection}",
        retrieved_at="2026-09-28T00:00:00Z",
        query_fingerprint=query.fingerprint,
    )
    return ApiQueryResult(
        collection=collection,
        items=tuple(items),
        partitions=(partition,),
        returned_count=len(items),
        unique_count=partition.unique_count,
        duplicate_ids=(),
        complete=True,
        query_fingerprint=query.fingerprint,
    )


class FakeApiProvider:
    def __init__(self, results: dict[str, ApiQueryResult]):
        self.results = results
        self.calls = []

    def query_events(self, _query: QuerySpec, **_options) -> ApiQueryResult:
        self.calls.append("events")
        return self.results["pdc-events"]

    def query_hazards(self, _query: QuerySpec, **_options) -> ApiQueryResult:
        self.calls.append("hazards")
        return self.results["pdc-hazards"]

    def query_impacts(self, _query: QuerySpec, **_options) -> ApiQueryResult:
        self.calls.append("impacts")
        return self.results["pdc-impacts"]


class LocalProfilerTests(unittest.TestCase):
    def test_duplicates_are_collection_scoped_and_payload_hashes_are_canonical(self) -> None:
        event = {
            "id": "shared-id",
            "collection": "pdc-events",
            "properties": {"datetime": "2024-02-01T00:00:00Z", "title": "Example"},
        }
        same_event_different_order = {
            "properties": {"title": "Example", "datetime": "2024-02-01T00:00:00Z"},
            "collection": "pdc-events",
            "id": "shared-id",
        }
        hazard = {
            "id": "shared-id",
            "collection": "pdc-hazards",
            "properties": {"datetime": "2024-01-01T00:00:00Z"},
        }

        with TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            source = temporary_path / "items.jsonl"
            source.write_text(
                "\n".join(json.dumps(item) for item in (event, same_event_different_order, hazard)) + "\n",
                encoding="utf-8",
            )
            report_path = temporary_path / "report.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "profile_local_pdc.py"),
                    "--path",
                    str(source),
                    "--output",
                    str(report_path),
                    "--work-dir",
                    str(temporary_path / "cache"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["records"]["duplicate_item_ids"], 1)
        self.assertEqual(report["records"]["duplicate_rows"], 1)
        self.assertEqual(report["records"]["payload_conflicts_for_duplicate_ids"], 0)
        self.assertEqual(
            report["coverage"]["datetime_fields"]["datetime"],
            {"min": "2024-01-01T00:00:00Z", "max": "2024-02-01T00:00:00Z", "count": 3},
        )


class LocalProviderTests(unittest.TestCase):
    def test_index_reuse_raw_reconstruction_and_change_detection(self) -> None:
        items = [fixture_json("pdc_event.json"), fixture_json("pdc_hazard.json"), *fixture_jsonl("pdc_impacts.jsonl")]
        query = QuerySpec(
            analysis_mode="country_detail",
            country_code="PHL",
            year=2024,
            months=(1,),
            categories=("people", "children_0_4"),
            source_mode="local_only",
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "pdc.jsonl"
            write_export(export, items)
            original_hash = sha256(export.read_bytes()).hexdigest()
            provider = local_provider(root, export)

            first = provider.build_index()
            second = provider.build_index()
            impacts = provider.query_impacts(query)

            self.assertFalse(first.reused)
            self.assertTrue(second.reused)
            self.assertEqual(first.items, 4)
            self.assertEqual(len(impacts.records), 2)
            self.assertEqual(sha256(export.read_bytes()).hexdigest(), original_hash)
            for record in impacts.records:
                with export.open("rb") as handle:
                    handle.seek(record.byte_offset)
                    self.assertEqual(json.loads(handle.read(record.byte_length)), record.item)

            with export.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(items[0], separators=(",", ":")) + "\n")
            rebuilt = provider.build_index()
            events = provider.query_events(query)

            self.assertFalse(rebuilt.reused)
            self.assertNotEqual(rebuilt.manifest_fingerprint, first.manifest_fingerprint)
            self.assertEqual(rebuilt.items, 5)
            self.assertEqual(events.duplicate_ids, (items[0]["id"],))
            coverage = json.loads((root / "coverage.json").read_text(encoding="utf-8"))
            self.assertEqual(coverage["coverage"]["duplicate_item_ids"], 1)

    def test_interrupted_unchanged_build_resumes_at_next_file(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export"
            export.mkdir()
            write_export(export / "01-events.jsonl", [fixture_json("pdc_event.json")])
            write_export(export / "02-hazards.jsonl", [fixture_json("pdc_hazard.json")])
            provider = local_provider(root, export)
            original = local_module._index_file
            calls = []

            def interrupt_second(connection, file_id, path):
                calls.append(path.name)
                if path.name == "02-hazards.jsonl":
                    raise OSError("simulated interruption")
                return original(connection, file_id, path)

            with patch("guard_pdc.local._index_file", side_effect=interrupt_second):
                with self.assertRaisesRegex(OSError, "simulated interruption"):
                    provider.build_index()
            resumed = provider.build_index()

            self.assertEqual(calls, ["01-events.jsonl", "02-hazards.jsonl"])
            self.assertTrue(resumed.resumed)
            self.assertEqual(resumed.items, 2)


class ServiceTests(unittest.TestCase):
    def test_no_event_stops_before_hazard_and_impact_queries(self) -> None:
        query = QuerySpec(
            analysis_mode="country_detail",
            country_code="PHL",
            year=2024,
            months=(1,),
            source_mode="api_only",
        )
        fake_api = FakeApiProvider(
            {
                collection: api_result(collection, [], query)
                for collection in ("pdc-events", "pdc-hazards", "pdc-impacts")
            }
        )
        result = PdcEvidenceService(MontandonConfig(api_token="test"), api_provider=fake_api).retrieve(query)

        self.assertEqual(fake_api.calls, ["events"])
        self.assertEqual(result.event_families, ())
        self.assertTrue(result.metadata.complete)

    def test_annual_overview_never_loads_local_impacts(self) -> None:
        items = [fixture_json("pdc_event.json"), fixture_json("pdc_hazard.json"), *fixture_jsonl("pdc_impacts.jsonl")]
        query = QuerySpec(
            analysis_mode="annual_country_overview",
            country_code=None,
            year=2024,
            months=(1,),
            source_mode="local_only",
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "pdc.jsonl"
            write_export(export, items)
            provider = local_provider(root, export)
            result = PdcEvidenceService(MontandonConfig(local_export_path=export), local_provider=provider).retrieve(query)

        self.assertEqual(len(result.event_snapshots), 1)
        self.assertEqual(result.impact_observations, ())

    def test_local_routing_normalizes_grains_and_related_links(self) -> None:
        items = [fixture_json("pdc_event.json"), fixture_json("pdc_hazard.json"), *fixture_jsonl("pdc_impacts.jsonl")]
        query = QuerySpec(
            analysis_mode="country_detail",
            country_code="PHL",
            year=2024,
            months=(1,),
            categories=("people", "children_0_4"),
            source_mode="local_only",
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "pdc.jsonl"
            write_export(export, items)
            config = MontandonConfig(local_export_path=export, local_index_path=root / "index.sqlite")
            service = PdcEvidenceService(config)
            self.assertIsNone(service.local_provider)
            result = service.retrieve(query)
            self.assertIsInstance(service.local_provider, PdcLocalProvider)

        self.assertEqual(len(result.event_families), 1)
        self.assertEqual(result.event_families[0].snapshot_count, 1)
        self.assertEqual(result.event_families[0].impact_observation_count, 2)
        self.assertEqual(len(result.event_snapshots), 1)
        self.assertEqual(len(result.impact_observations), 2)
        self.assertTrue(all(row.event_item_id == items[0]["id"] for row in result.impact_observations))
        self.assertTrue(all(edge.status == "validated" for edge in result.correlation_evidence))
        self.assertTrue(all(value.availability == "local_only" for row in result.impact_observations for value in row.provenance))

    def test_filtered_out_impact_links_are_not_reported_as_missing(self) -> None:
        items = [fixture_json("pdc_event.json"), fixture_json("pdc_hazard.json"), *fixture_jsonl("pdc_impacts.jsonl")]
        query = QuerySpec(
            analysis_mode="country_detail",
            country_code="PHL",
            year=2024,
            months=(1,),
            categories=("children_0_4",),
            source_mode="local_only",
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "pdc.jsonl"
            write_export(export, items)
            provider = local_provider(root, export)
            result = PdcEvidenceService(MontandonConfig(local_export_path=export), local_provider=provider).retrieve(query)

        self.assertEqual(len(result.impact_observations), 1)
        self.assertEqual(result.quality_summary["missing_companions"], 0)

    def test_multi_detail_impact_rows_keep_detail_positions(self) -> None:
        event = fixture_json("pdc_event.json")
        impact = fixture_jsonl("pdc_impacts.jsonl")[0]
        people = impact["properties"]["monty:impact_detail"]
        child = dict(people, category="children_0_4", value=80)
        impact["properties"]["monty:impact_detail"] = [people, child]
        query = QuerySpec(
            analysis_mode="country_detail",
            country_code="PHL",
            year=2024,
            months=(1,),
            categories=("people", "children_0_4"),
            source_mode="local_only",
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "multi-detail.jsonl"
            write_export(export, [event, impact])
            provider = local_provider(root, export)
            result = PdcEvidenceService(MontandonConfig(local_export_path=export), local_provider=provider).retrieve(query)

        self.assertEqual(len(result.impact_observations), 2)
        self.assertEqual(
            {(row.category, row.detail_position) for row in result.impact_observations},
            {("people", 0), ("children_0_4", 1)},
        )

    def test_legacy_fallback_keeps_many_to_many_candidates_ambiguous(self) -> None:
        first = fixture_json("pdc_event.json")
        first["id"] = "legacy-event-1"
        first["properties"].pop("monty:src_event_id")
        first["links"] = []
        second = deepcopy(first)
        second["id"] = "legacy-event-2"
        impact = fixture_jsonl("legacy_missing_src_event_id.jsonl")[0]
        query = QuerySpec(
            analysis_mode="country_detail",
            country_code="PHL",
            year=2024,
            months=(1,),
            source_mode="local_only",
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "legacy.jsonl"
            write_export(export, [first, second, impact])
            provider = local_provider(root, export)
            result = PdcEvidenceService(MontandonConfig(local_export_path=export), local_provider=provider).retrieve(query)

        fallback = [edge for edge in result.correlation_evidence if edge.method == "legacy_corr_id_fallback"]
        self.assertEqual(len(fallback), 2)
        self.assertTrue(all(edge.status == "ambiguous" for edge in fallback))
        self.assertIsNone(result.impact_observations[0].event_item_id)
        self.assertEqual(result.event_families[0].snapshot_count, 2)

    def test_related_source_id_mismatch_is_visible_as_conflict(self) -> None:
        event = fixture_json("pdc_event.json")
        hazard = fixture_json("pdc_hazard.json")
        hazard["properties"]["monty:src_event_id"] = "different-source"
        query = QuerySpec(
            analysis_mode="country_detail",
            country_code="PHL",
            year=2024,
            months=(1,),
            source_mode="local_only",
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "mismatch.jsonl"
            write_export(export, [event, hazard])
            provider = local_provider(root, export)
            result = PdcEvidenceService(MontandonConfig(local_export_path=export), local_provider=provider).retrieve(query)

        hazard_edges = [edge for edge in result.correlation_evidence if edge.target_collection == "pdc-hazards"]
        self.assertEqual(len(hazard_edges), 1)
        self.assertEqual(hazard_edges[0].status, "conflict")

    def test_compare_merges_identical_payloads_and_retains_conflicts(self) -> None:
        event = fixture_json("pdc_event.json")
        hazard = fixture_json("pdc_hazard.json")
        impacts = fixture_jsonl("pdc_impacts.jsonl")
        api_impacts = deepcopy(impacts)
        api_impacts[0]["properties"]["monty:impact_detail"]["value"] = 999
        query = QuerySpec(
            analysis_mode="country_detail",
            country_code="PHL",
            year=2024,
            months=(1,),
            categories=("people", "children_0_4"),
            source_mode="compare",
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "pdc.jsonl"
            write_export(export, [event, hazard, *impacts])
            provider = local_provider(root, export)
            fake_api = FakeApiProvider(
                {
                    "pdc-events": api_result("pdc-events", [event], query),
                    "pdc-hazards": api_result("pdc-hazards", [hazard], query),
                    "pdc-impacts": api_result("pdc-impacts", api_impacts, query),
                }
            )
            config = MontandonConfig(
                api_token="test",
                api_cache_path=root / "api-cache",
                local_export_path=export,
                local_index_path=root / "index.sqlite",
            )
            result = PdcEvidenceService(config, api_provider=fake_api, local_provider=provider).retrieve(query)

        self.assertEqual(fake_api.calls, ["events", "hazards", "impacts"])
        self.assertEqual(len(result.event_snapshots), 1)
        self.assertEqual({value.provider for value in result.event_snapshots[0].provenance}, {"api", "local"})
        people = [row for row in result.impact_observations if row.category == "people"]
        self.assertEqual(len(people), 2)
        self.assertTrue(all(value.availability == "payload_conflict" for row in people for value in row.provenance))
        self.assertGreaterEqual(result.quality_summary["payload_conflicts"], 2)


if __name__ == "__main__":
    unittest.main()
