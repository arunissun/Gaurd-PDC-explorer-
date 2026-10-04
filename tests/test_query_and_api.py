"""Stage 2 tests for query validation, taxonomy, and evidence row contracts."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError

from guard_pdc.api import ApiError, PageSummary, PdcApiProvider, build_search_body
from guard_pdc.config import ConfigError, MontandonConfig

from guard_pdc.models import (
    CorrelationEvidenceRow,
    EventFamilyRow,
    EventSnapshotRow,
    ImpactObservationRow,
    QuerySpec,
    ValidationError,
)
from guard_pdc.taxonomy import (
    AGE_BAND_CATEGORIES,
    FLOOD_CODES,
    category_group,
    category_label,
    hazard_codes_for_label,
    hazard_label,
    ordered_categories,
)


ROOT = Path(__file__).parent


def read_json(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, document: dict):
        self.document = document

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.document).encode("utf-8")


class SequenceOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, request, timeout=None):
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response)


class QuerySpecTests(unittest.TestCase):
    def test_country_detail_is_canonical_and_deterministic(self) -> None:
        first = QuerySpec(
            analysis_mode="country_detail",
            country_code="phl",
            year=2024,
            months=(3, 1, 3),
            hazard_codes=("nat-hyd-flo-flo", "MH0600", "FL"),
            categories=("children_0_4", "people"),
        )
        second = QuerySpec(
            analysis_mode="country_detail",
            country_code="PHL",
            year=2024,
            months=(1, 3),
            hazard_codes=("FL", "MH0600", "nat-hyd-flo-flo"),
            categories=("people", "children_0_4"),
        )

        self.assertEqual(first, second)
        self.assertEqual(first.country_code, "PHL")
        self.assertEqual(first.months, (1, 3))
        self.assertEqual(first.hazard_codes, FLOOD_CODES)
        self.assertEqual(first.categories, ("people", "children_0_4"))
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertTrue(first.retrieves_impact_detail)

    def test_annual_overview_is_safe_without_country(self) -> None:
        query = QuerySpec(
            analysis_mode="annual_country_overview",
            country_code=None,
            year=2025,
            months=tuple(range(1, 13)),
        )

        self.assertIsNone(query.country_code)
        self.assertFalse(query.retrieves_impact_detail)
        self.assertEqual(query.warnings, ())

    def test_2026_is_allowed_but_warned(self) -> None:
        query = QuerySpec(
            analysis_mode="country_detail",
            country_code="PHL",
            year=2026,
            months=(1,),
        )

        self.assertTrue(query.is_partial_year)
        self.assertEqual(len(query.warnings), 1)

    def test_invalid_requests_fail_before_provider_access(self) -> None:
        invalid = (
            dict(analysis_mode="country_detail", country_code=None, year=2024, months=(1,)),
            dict(analysis_mode="country_detail", country_code="PHL", year=2024, months=()),
            dict(analysis_mode="country_detail", country_code="PHL", year=1999, months=(1,)),
            dict(analysis_mode="country_detail", country_code="PHL", year=2024, months=(13,)),
            dict(analysis_mode="annual_country_overview", country_code=None, year=2024, months=(1,), categories=("schools",)),
            dict(analysis_mode="country_detail", country_code="PHL", year=2024, months=(1,), categories=("not_a_pdc_category",)),
        )

        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    QuerySpec(**values)


class TaxonomyTests(unittest.TestCase):
    def test_labels_groups_and_order_are_stable(self) -> None:
        self.assertEqual(ordered_categories(("hospitals", "people", "children_0_4")), ("people", "children_0_4", "hospitals"))
        self.assertEqual(category_label("people"), "People")
        self.assertEqual(category_group("children_0_4"), "Age")
        self.assertEqual(len(AGE_BAND_CATEGORIES), 14)
        self.assertEqual(hazard_label("FL"), "Flood")
        self.assertEqual(hazard_codes_for_label("Flood"), FLOOD_CODES)


class EvidenceRowTests(unittest.TestCase):
    def test_fixture_values_and_null_unit_are_preserved(self) -> None:
        event = read_json("pdc_event.json")
        hazard = read_json("pdc_hazard.json")
        impact = json.loads((ROOT / "fixtures" / "pdc_impacts.jsonl").read_text(encoding="utf-8").splitlines()[0])

        event_properties = event["properties"]
        impact_properties = impact["properties"]
        event_id = event["id"]
        source_id = event_properties["monty:src_event_id"]

        family = EventFamilyRow(
            family_key=source_id,
            source_event_id=source_id,
            title=event_properties["title"],
            country_codes=tuple(event_properties["monty:country_codes"]),
            hazard_codes=tuple(event_properties["monty:hazard_codes"]),
            snapshot_count=1,
            geometry_types=(event["geometry"]["type"],),
            providers=("fixture",),
        )
        snapshot = EventSnapshotRow(
            event_item_id=event_id,
            source_event_id=source_id,
            item_datetime=event_properties["datetime"],
            episode_number=event_properties["monty:episode_number"],
            geometry=event["geometry"],
            original_properties=event_properties,
        )
        observation = ImpactObservationRow(
            impact_item_id=impact["id"],
            event_item_id=event_id,
            source_event_id=source_id,
            country_code=impact_properties["monty:country_codes"][0],
            impact_type=impact_properties["monty:impact_detail"]["type"],
            category=impact_properties["monty:impact_detail"]["category"],
            original_value=impact_properties["monty:impact_detail"]["value"],
            numeric_value=impact_properties["monty:impact_detail"]["value"],
            original_unit=impact_properties["monty:impact_detail"]["unit"],
            estimate_type=impact_properties["monty:impact_detail"]["estimate_type"],
            item_datetime=impact_properties["datetime"],
            geometry=impact["geometry"],
            original_properties=impact_properties,
        )
        edge = CorrelationEvidenceRow(
            source_collection=event["collection"],
            source_item_id=event_id,
            target_collection=hazard["collection"],
            target_item_id=hazard["id"],
            source_event_id=source_id,
            target_source_event_id=hazard["properties"]["monty:src_event_id"],
            related_href=event["links"][0]["href"],
            related_role=event["links"][0]["rel"],
            episode_compatible=True,
            time_compatible=True,
            method="related_link",
            status="validated",
        )

        self.assertEqual(family.family_key, source_id)
        self.assertEqual(snapshot.geometry["type"], "Point")
        self.assertEqual(observation.original_value, 1200)
        self.assertIsNone(observation.original_unit)
        self.assertEqual(edge.status, "validated")


class ApiProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.query = QuerySpec(
            analysis_mode="country_detail",
            country_code="PHL",
            year=2024,
            months=(1,),
            source_mode="api_only",
        )

    def test_api_endpoint_must_use_https(self) -> None:
        with self.assertRaises(ConfigError):
            MontandonConfig(endpoint="http://montandon-eoapi.ifrc.org/stac", api_token="test")

    def test_uncached_query_writes_and_reloads_completed_cache(self) -> None:
        document = {
            "type": "FeatureCollection",
            "features": [{"id": "event-1", "collection": "pdc-events"}],
            "links": [],
        }
        with TemporaryDirectory() as temporary:
            config = MontandonConfig(api_token="test", api_cache_path=Path(temporary))
            first = PdcApiProvider(config, opener=SequenceOpener(document), sleeper=lambda _: None).query_events(self.query)
            second = PdcApiProvider(config, opener=SequenceOpener(), sleeper=lambda _: None).query_events(self.query)

            self.assertFalse(first.from_cache)
            self.assertTrue(second.from_cache)
            self.assertEqual(second.items, first.items)
            self.assertEqual(second.query_fingerprint, self.query.fingerprint)
            self.assertIsInstance(second.partitions[0].pages[0], PageSummary)
            self.assertEqual(second.partitions[0].query_fingerprint, self.query.fingerprint)

    def test_cache_identity_ignores_view_and_refresh_controls(self) -> None:
        view_query = QuerySpec(
            analysis_mode="country_detail",
            country_code="PHL",
            year=2024,
            months=(1,),
            source_mode="api_only",
            include_zero_values=False,
            include_missing_geometry=False,
        )
        refresh_query = QuerySpec(
            analysis_mode="country_detail",
            country_code="PHL",
            year=2024,
            months=(1,),
            source_mode="api_only",
            refresh_api_cache=True,
        )
        provider = PdcApiProvider(MontandonConfig(api_token="test"))
        body = build_search_body(
            self.query,
            "pdc-events",
            start="2024-01-01T00:00:00Z",
            end="2024-02-01T00:00:00Z",
        )

        expected = provider._cache_key(self.query, "pdc-events", body)
        self.assertEqual(expected, provider._cache_key(view_query, "pdc-events", body))
        self.assertEqual(expected, provider._cache_key(refresh_query, "pdc-events", body))
        self.assertEqual(self.query.fingerprint, view_query.fingerprint)
        self.assertEqual(self.query.fingerprint, refresh_query.fingerprint)

    def test_pagination_follows_same_origin_post_body(self) -> None:
        first_page = {
            "type": "FeatureCollection",
            "features": [{"id": "event-1"}],
            "links": [
                {
                    "rel": "next",
                    "href": "https://montandon-eoapi.ifrc.org/stac/search",
                    "method": "POST",
                    "body": {"token": "next-page"},
                }
            ],
        }
        second_page = {
            "type": "FeatureCollection",
            "features": [{"id": "event-2"}],
            "links": [
                {
                    "rel": "next",
                    "href": "https://montandon-eoapi.ifrc.org/stac/search",
                    "method": "POST",
                    "body": {"token": "third-page"},
                }
            ],
        }
        third_page = {
            "type": "FeatureCollection",
            "features": [{"id": "event-3"}],
            "links": [],
        }
        opener = SequenceOpener(first_page, second_page, third_page)
        provider = PdcApiProvider(MontandonConfig(api_token="test"), opener=opener, sleeper=lambda _: None)

        items, pages, _, _, complete, reason, _ = provider._fetch_documents(
            url=provider.config.url("search"),
            method="POST",
            body={"collections": ["pdc-events"]},
            item_cap=None,
        )

        self.assertEqual([item["id"] for item in items], ["event-1", "event-2", "event-3"])
        self.assertEqual(len(pages), 3)
        self.assertTrue(complete)
        self.assertEqual(reason, "pagination_exhausted")

    def test_identical_post_continuation_is_rejected_without_repeating_request(self) -> None:
        body = {"token": "same-page"}
        page = {
            "type": "FeatureCollection",
            "features": [{"id": "event-1"}],
            "links": [{"rel": "next", "href": "https://montandon-eoapi.ifrc.org/stac/search", "method": "POST", "body": body}],
        }
        opener = SequenceOpener(page)
        provider = PdcApiProvider(MontandonConfig(api_token="test"), opener=opener)
        _, pages, _, _, complete, reason, _ = provider._fetch_documents(
            url=provider.config.url("search"), method="POST", body=body, item_cap=None,
        )
        self.assertFalse(complete)
        self.assertEqual(reason, "pagination_loop")
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(opener.calls), 1)

    def test_external_next_link_is_rejected_before_request(self) -> None:
        opener = SequenceOpener(
            {
                "type": "FeatureCollection",
                "features": [{"id": "event-1"}],
                "links": [{"rel": "next", "href": "https://external.invalid/page"}],
            }
        )
        provider = PdcApiProvider(MontandonConfig(api_token="test"), opener=opener, sleeper=lambda _: None)

        with self.assertRaisesRegex(ApiError, "outside the configured API origin"):
            provider._fetch_documents(
                url=provider.config.url("search"),
                method="POST",
                body={"collections": ["pdc-events"]},
                item_cap=None,
            )
        self.assertEqual(len(opener.calls), 1)

    def test_final_page_over_cap_is_incomplete(self) -> None:
        opener = SequenceOpener(
            {
                "type": "FeatureCollection",
                "features": [{"id": "1"}, {"id": "2"}, {"id": "3"}],
                "links": [],
            }
        )
        provider = PdcApiProvider(
            MontandonConfig(api_token="test"),
            partition_cap=2,
            opener=opener,
            sleeper=lambda _: None,
        )

        items, _, _, _, complete, reason, _ = provider._fetch_documents(
            url=provider.config.url("search"),
            method="POST",
            body={"collections": ["pdc-events"]},
            item_cap=2,
        )

        self.assertEqual(len(items), 3)
        self.assertFalse(complete)
        self.assertEqual(reason, "partition_cap")

    def test_transient_http_error_is_retried(self) -> None:
        error = HTTPError("https://montandon-eoapi.ifrc.org/stac/queryables", 503, "busy", None, None)
        opener = SequenceOpener(error, {"properties": {}})
        sleeps = []
        provider = PdcApiProvider(MontandonConfig(api_token="test"), opener=opener, sleeper=sleeps.append)

        self.assertEqual(provider._request_json(provider.config.url("queryables")), {"properties": {}})
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(sleeps, [1])

    def test_full_item_lookup_uses_post_search_without_projection(self) -> None:
        opener = SequenceOpener(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "id": "event-1",
                        "collection": "pdc-events",
                        "properties": {"unprojected": "kept"},
                    }
                ],
                "links": [],
            }
        )
        provider = PdcApiProvider(MontandonConfig(api_token="test"), opener=opener, sleeper=lambda _: None)

        item = provider.get_item("pdc-events", "event-1")
        request = opener.calls[0]
        body = json.loads(request.data)

        self.assertEqual(item["properties"]["unprojected"], "kept")
        self.assertEqual(request.get_method(), "POST")
        self.assertNotIn("fields", body)

    def test_capability_discovery_uses_completed_cache(self) -> None:
        responses = [{"properties": {}}]
        for collection in ("pdc-events", "pdc-hazards", "pdc-impacts"):
            responses.extend(({"id": collection}, {"properties": {}}))
        with TemporaryDirectory() as temporary:
            config = MontandonConfig(api_token="test", api_cache_path=Path(temporary))
            first_opener = SequenceOpener(*responses)
            first = PdcApiProvider(config, opener=first_opener, sleeper=lambda _: None).discover()
            second = PdcApiProvider(config, opener=SequenceOpener(), sleeper=lambda _: None).discover()

            self.assertFalse(first.from_cache)
            self.assertTrue(second.from_cache)
            self.assertEqual(first.collections, second.collections)
            self.assertEqual(len(first_opener.calls), 7)


if __name__ == "__main__":
    unittest.main()
