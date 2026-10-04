"""Regressions for the 2026-10-04 code-review fixes (no network beyond localhost)."""

from __future__ import annotations

from copy import deepcopy
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request
import warnings

from guard_pdc.analysis import build_analysis_frames
from guard_pdc.api import ApiError, PdcApiProvider, open_without_redirects
from guard_pdc.config import MontandonConfig
from guard_pdc.maps import fetch_alert_areas
from guard_pdc.models import QuerySpec
from guard_pdc.service import PdcEvidenceService
from guard_pdc.visuals import _completeness_data
from tests.test_query_and_api import SequenceOpener
from tests.test_retrieval_resilience import ScriptedOpener, http_error, page, provider, query
from tests.test_stage6_to_8 import FakeApiProvider, api_result, fixture_json, fixture_jsonl

API_SEARCH = "https://montandon-eoapi.ifrc.org/stac/search"


class _RedirectServer:
    """Localhost server whose /start redirects to /elsewhere and records visits."""

    def __init__(self) -> None:
        visits: list[tuple[str, str | None]] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                visits.append((self.path, self.headers.get("Authorization")))
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/elsewhere")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b"{}")

            def log_message(self, *_args) -> None:
                return

        self.visits = visits
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "_RedirectServer":
        self.thread.start()
        return self

    def __exit__(self, *_args) -> None:
        self.server.shutdown()
        self.server.server_close()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.server.server_port}{path}"


class RedirectTests(unittest.TestCase):
    def test_redirect_is_refused_and_credentials_never_reach_the_target(self) -> None:
        with _RedirectServer() as server:
            request = Request(server.url("/start"), headers={"Authorization": "Bearer secret-value"})
            with self.assertRaises(HTTPError) as caught:
                open_without_redirects(request, timeout=5)
            self.assertEqual(caught.exception.code, 302)
            caught.exception.close()
        self.assertEqual([path for path, _ in server.visits], ["/start"])

    def test_api_provider_refuses_redirects_by_default_and_surfaces_them(self) -> None:
        config = MontandonConfig(api_token="test")
        self.assertIs(PdcApiProvider(config)._opener, open_without_redirects)
        opener = SequenceOpener(HTTPError(API_SEARCH, 302, "Found", {}, None))
        with self.assertRaises(ApiError) as caught:
            PdcApiProvider(config, opener=opener, sleeper=lambda _: None)._request_json(API_SEARCH, method="POST", body={})
        self.assertEqual(caught.exception.status, 302)
        self.assertFalse(caught.exception.recoverable)
        self.assertEqual(len(opener.calls), 1)

    def test_alert_area_redirect_is_reported_not_followed(self) -> None:
        def redirecting(_request, timeout=None):
            raise HTTPError("https://assets.example/maps.json", 302, "Found", {}, None)

        areas = fetch_alert_areas("https://assets.example/maps.json", allowed_hosts=("assets.example",), opener=redirecting)
        self.assertEqual(areas.status, "not_allowed")
        self.assertIn("redirect", areas.message)


class ApiRobustnessTests(unittest.TestCase):
    def test_connection_dropped_mid_body_is_a_recoverable_api_error(self) -> None:
        opener = SequenceOpener(*[http.client.IncompleteRead(b"partial")] * 4)
        api = PdcApiProvider(MontandonConfig(api_token="test"), opener=opener, sleeper=lambda _: None)
        with self.assertRaises(ApiError) as caught:
            api._request_json(API_SEARCH, method="POST", body={})
        self.assertTrue(caught.exception.recoverable)
        self.assertEqual(len(opener.calls), 4)

    def test_dropped_connection_becomes_a_failed_window_not_a_crash(self) -> None:
        opener = ScriptedOpener(lambda _body: http.client.IncompleteRead(b""))
        result = provider(opener, failure_budget=2, max_workers=1).query_events(query())
        self.assertFalse(result.complete)
        self.assertTrue(result.failures)

    def test_fatal_error_cancels_windows_still_queued(self) -> None:
        def unauthorized(_body):
            time.sleep(0.05)
            return http_error(401)

        opener = ScriptedOpener(unauthorized)
        with self.assertRaises(ApiError):
            provider(opener, max_workers=2).query_events(query(months=tuple(range(1, 13))))
        # Only the windows already running finish; the other queued months are never sent.
        self.assertLess(len(opener.bodies), 12)
        self.assertLessEqual(len(opener.bodies), 4)

    def test_cache_never_merges_pages_from_an_older_longer_retrieval(self) -> None:
        def first_page(*ids: str, continuation: bool) -> dict:
            document = page(*ids)
            if continuation:
                document["links"] = [{"rel": "next", "href": API_SEARCH, "method": "POST", "body": {"token": "page-2"}}]
            return document

        with TemporaryDirectory() as temporary:
            config = MontandonConfig(api_token="test", api_cache_path=Path(temporary))
            fresh = query(refresh_api_cache=True)
            two_pages = SequenceOpener(first_page("old-1", continuation=True), page("old-2"))
            PdcApiProvider(config, opener=two_pages, sleeper=lambda _: None).query_events(fresh)
            one_page = SequenceOpener(first_page("new-1", continuation=False))
            PdcApiProvider(config, opener=one_page, sleeper=lambda _: None).query_events(fresh)

            cached = PdcApiProvider(config, opener=SequenceOpener(), sleeper=lambda _: None).query_events(query(refresh_api_cache=False))
        self.assertTrue(cached.from_cache)
        self.assertEqual([item["id"] for item in cached.items], ["new-1"])


class CompletenessTests(unittest.TestCase):
    def test_completeness_never_exceeds_100_percent_with_two_impact_types(self) -> None:
        # One category observed under both impact types used to give 200% bars.
        event = fixture_json("pdc_event.json")
        hazard = fixture_json("pdc_hazard.json")
        impacts = deepcopy(fixture_jsonl("pdc_impacts.jsonl"))[:1]
        as_cost = deepcopy(impacts[0])
        as_cost["id"] = "pdc-impact-fixture-people-cost-PHL"
        as_cost["properties"]["monty:impact_detail"]["type"] = "cost"
        spec = QuerySpec(
            analysis_mode="country_detail", country_code="PHL", year=2024, months=(1,), source_mode="api_only",
            impact_types=("affected_total", "cost"), categories=("people",),
        )
        fake = FakeApiProvider({
            "pdc-events": api_result("pdc-events", [event], spec),
            "pdc-hazards": api_result("pdc-hazards", [hazard], spec),
            "pdc-impacts": api_result("pdc-impacts", [*impacts, as_cost], spec),
        })
        result = PdcEvidenceService(MontandonConfig(api_token="fixture"), api_provider=fake).retrieve(spec)
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            frames = build_analysis_frames(result)
        totals = _completeness_data(frames).groupby("family_key")["percent"].sum()
        self.assertFalse(totals.empty)
        self.assertTrue((totals.round(6) == 100).all(), totals.to_dict())


if __name__ == "__main__":
    unittest.main()
