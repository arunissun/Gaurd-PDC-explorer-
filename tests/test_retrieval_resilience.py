"""Multi-year contract and adaptive, partial-result API retrieval (no network)."""

from __future__ import annotations

import json
from tempfile import TemporaryDirectory
from pathlib import Path
import threading
import unittest
from urllib.error import HTTPError

from guard_pdc.api import ApiError, PdcApiProvider, build_search_body
from guard_pdc.config import MontandonConfig
from guard_pdc.models import QuerySpec, ValidationError
from guard_pdc.service import PdcEvidenceService
from guard_pdc.taxonomy import CATEGORY_ORDER
from tests.test_query_and_api import FakeResponse


def body_of(request) -> dict:
    return json.loads(request.data) if request.data else {}


def window_start(body: dict) -> str | None:
    for clause in body.get("filter", {}).get("args", []):
        if clause.get("op") == ">=":
            return clause["args"][1]
    return None


def page(*ids: str, collection: str = "pdc-events", datetime: str = "2024-01-10T00:00:00Z") -> dict:
    return {
        "type": "FeatureCollection",
        "features": [{"id": item_id, "collection": collection, "properties": {"datetime": datetime}} for item_id in ids],
        "links": [],
    }


class ScriptedOpener:
    """Thread-safe opener driven by a function of the decoded request body."""

    def __init__(self, respond):
        self.respond = respond
        self.bodies: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, request, timeout=None):
        body = body_of(request)
        with self._lock:
            self.bodies.append(body)
        response = self.respond(body)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response)


def http_error(code: int) -> HTTPError:
    return HTTPError("https://montandon-eoapi.ifrc.org/stac/search", code, "error", {}, None)


def provider(opener, **options) -> PdcApiProvider:
    temporary = TemporaryDirectory()
    config = MontandonConfig(api_token="test", api_cache_path=Path(temporary.name))
    instance = PdcApiProvider(config, opener=opener, sleeper=lambda _seconds: None, **options)
    instance._temporary = temporary  # keep the cache directory alive with the provider
    return instance


def query(**overrides) -> QuerySpec:
    values = dict(analysis_mode="country_detail", country_code="PHL", year=2024, months=(1,), source_mode="api_only", refresh_api_cache=True)
    values.update(overrides)
    return QuerySpec(**values)


class MultiYearQueryTests(unittest.TestCase):
    def test_single_year_fingerprint_is_unchanged_by_the_new_field(self) -> None:
        single = query()
        explicit = query(end_year=2024)
        self.assertEqual(single, explicit)
        self.assertIsNone(explicit.end_year)
        self.assertNotIn("end_year", single.retrieval_dict())
        self.assertEqual(single.fingerprint, explicit.fingerprint)

    def test_five_year_period_windows_and_label(self) -> None:
        period = query(year=2022, end_year=2026, months=tuple(range(1, 13)))
        self.assertEqual(period.years, (2022, 2023, 2024, 2025, 2026))
        self.assertEqual(len(period.windows()), 60)
        self.assertEqual(period.windows()[0], (2022, 1))
        self.assertEqual(period.windows()[-1], (2026, 12))
        self.assertEqual(period.period_label, "2022–2026 · Jan–Dec")
        self.assertTrue(period.is_partial_year)
        self.assertNotEqual(period.fingerprint, query(year=2022, months=tuple(range(1, 13))).fingerprint)
        self.assertEqual(query(months=(6, 7, 8, 9)).period_label, "2024 · Jun–Sep")
        self.assertEqual(query(months=(1, 3)).period_label, "2024 · Jan, Mar")

    def test_periods_over_five_years_or_reversed_are_rejected(self) -> None:
        for overrides in (
            dict(year=2021, end_year=2026),
            dict(year=2024, end_year=2023),
            dict(year=2024, end_year=2027),
            dict(analysis_mode="annual_country_overview", country_code=None, year=2023, end_year=2024),
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                query(**overrides)


class AdaptiveRetrievalTests(unittest.TestCase):
    def test_default_page_size_and_all_category_clause_omission(self) -> None:
        everything = query(impact_types=("affected_total", "cost"), categories=CATEGORY_ORDER)
        body = build_search_body(everything, "pdc-impacts", start="2024-01-01T00:00:00Z", end="2024-02-01T00:00:00Z")
        self.assertEqual(body["limit"], 250)
        properties = json.dumps(body["filter"])
        self.assertNotIn("monty:impact_detail", properties)
        narrow = build_search_body(query(), "pdc-impacts", start="2024-01-01T00:00:00Z", end="2024-02-01T00:00:00Z")
        self.assertIn("monty:impact_detail.category", json.dumps(narrow["filter"]))

    def test_persistent_500_bisects_the_window_and_completes(self) -> None:
        def respond(body):
            start = window_start(body)
            if start == "2024-01-01T00:00:00Z" and body.get("limit") == 250 and len(opener.bodies) <= 4:
                return http_error(500)
            return page(f"event-{start}")

        opener = ScriptedOpener(respond)
        result = provider(opener).query_events(query())
        self.assertTrue(result.complete)
        self.assertEqual(len(result.partitions), 2)
        self.assertTrue(all("bisect_window" in partition.adaptations for partition in result.partitions))
        self.assertEqual(result.partitions[0].start, "2024-01-01T00:00:00Z")
        self.assertEqual(result.partitions[1].end, "2024-02-01T00:00:00Z")
        self.assertEqual(result.partitions[0].end, result.partitions[1].start)
        metadata = result.metadata(query(), "https://montandon-eoapi.ifrc.org/stac")
        self.assertTrue(any("completed after API errors" in warning for warning in metadata.warnings))
        self.assertEqual(metadata.failed_partitions, ())

    def test_first_page_400_shrinks_pages_without_bisecting(self) -> None:
        def respond(body):
            return http_error(400) if body.get("limit") == 250 else page("event-1")

        opener = ScriptedOpener(respond)
        result = provider(opener).query_events(query())
        self.assertTrue(result.complete)
        self.assertEqual([body["limit"] for body in opener.bodies], [250, 100])
        self.assertEqual(result.partitions[0].adaptations, ("smaller_pages",))

    def test_impact_filter_is_split_after_smallest_pages_still_fail(self) -> None:
        def respond(body):
            categories = [clause for clause in json.dumps(body.get("filter")).split("monty:impact_detail.category")]
            if body.get("limit") in (250, 100) or len(categories) > 2:
                return http_error(413)
            return page(f"impact-{len(opener.bodies)}", collection="pdc-impacts")

        opener = ScriptedOpener(respond)
        impacts = query(categories=("people", "households"))
        result = provider(opener).query_impacts(impacts)
        self.assertTrue(result.complete)
        self.assertEqual(sorted(partition.filter_part for partition in result.partitions), ["affected_total:households", "affected_total:people"])
        self.assertTrue(all("split_filter" in partition.adaptations for partition in result.partitions))

    def test_unrecoverable_failure_is_partial_not_an_exception(self) -> None:
        opener = ScriptedOpener(lambda _body: http_error(503))
        result = provider(opener, failure_budget=3, max_workers=1).query_events(query(months=(1, 2)))
        self.assertFalse(result.complete)
        self.assertTrue(result.failures)
        metadata = result.metadata(query(months=(1, 2)), "https://montandon-eoapi.ifrc.org/stac")
        self.assertFalse(metadata.complete)
        self.assertTrue(metadata.failed_partitions)
        self.assertTrue(any("incomplete" in warning for warning in metadata.warnings))
        # The budget bounds how hard a failing API is hit: 3 adaptive attempts x 4 in-place tries.
        self.assertLessEqual(len(opener.bodies), 3 * 4)

    def test_authentication_failure_raises_immediately(self) -> None:
        opener = ScriptedOpener(lambda _body: http_error(401))
        with self.assertRaises(ApiError) as caught:
            provider(opener).query_events(query())
        self.assertEqual(caught.exception.status, 401)
        self.assertEqual(len(opener.bodies), 1)

    def test_retry_reuses_completed_windows_and_requests_only_failed_ones(self) -> None:
        def failing_february(body):
            return http_error(503) if window_start(body).startswith("2024-02") else page(f"event-{window_start(body)}")

        first_opener = ScriptedOpener(failing_february)
        two_months = query(months=(1, 2))
        first = provider(first_opener, failure_budget=1, max_workers=1).query_events(two_months)
        self.assertFalse(first.complete)

        second_opener = ScriptedOpener(lambda body: page(f"event-{window_start(body)}"))
        second = provider(second_opener).query_events(two_months, reuse=first.window_results)
        self.assertTrue(second.complete)
        self.assertTrue(all(window_start(body).startswith("2024-02") for body in second_opener.bodies))
        reused = [partition for partition in second.partitions if "reused_from_previous_attempt" in partition.adaptations]
        self.assertEqual(len(reused), 1)
        self.assertEqual(len(second.items), 2)

    def test_parallel_and_sequential_retrieval_merge_identically(self) -> None:
        months = tuple(range(1, 13))

        def respond(body):
            start = window_start(body)
            return page(f"event-{start}-a", f"event-{start}-b")

        sequential = provider(ScriptedOpener(respond), max_workers=1).query_events(query(months=months))
        parallel = provider(ScriptedOpener(respond), max_workers=4).query_events(query(months=months))
        self.assertEqual([item["id"] for item in sequential.items], [item["id"] for item in parallel.items])
        self.assertEqual(len(parallel.items), 24)

    def test_progress_is_reported_once_per_window(self) -> None:
        events = []
        provider(ScriptedOpener(lambda body: page(f"event-{window_start(body)}")), max_workers=3).query_events(
            query(months=(1, 2, 3)), on_progress=events.append
        )
        self.assertEqual(len(events), 3)
        self.assertEqual(sorted(event.completed_windows for event in events), [1, 2, 3])
        self.assertTrue(all(event.total_windows == 3 for event in events))


class ServiceWindowTests(unittest.TestCase):
    def test_hazards_and_impacts_are_requested_only_for_event_months(self) -> None:
        def respond(body):
            collection = body["collections"][0]
            start = window_start(body)
            if collection == "pdc-events":
                return page("pdc-event-1", datetime="2024-02-05T00:00:00Z") if start.startswith("2024-02") else page()
            return page(f"{collection}-1", collection=collection, datetime="2024-02-03T00:00:00Z")

        opener = ScriptedOpener(respond)
        three_months = query(months=(1, 2, 3))
        service = PdcEvidenceService(MontandonConfig(api_token="test"), api_provider=provider(opener))
        result = service.retrieve(three_months)
        hazard_and_impact_starts = {window_start(body) for body in opener.bodies if body["collections"][0] != "pdc-events"}
        self.assertEqual(hazard_and_impact_starts, {"2024-02-01T00:00:00Z"})
        self.assertEqual(set(result.retrieval_state), {"pdc-events", "pdc-hazards", "pdc-impacts"})
        self.assertTrue(result.complete)


if __name__ == "__main__":
    unittest.main()
