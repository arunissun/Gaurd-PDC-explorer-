"""Package pieces the notebook relies on: the single-request wrapper and token lookup (no network)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from guard_pdc.api import ApiError, PdcApiProvider
from guard_pdc.config import TOKEN_VARIABLE, MontandonConfig, find_token
from tests.test_query_and_api import FakeResponse
from tests.test_retrieval_resilience import http_error

BASE = "https://montandon-eoapi.ifrc.org/stac"
TOKEN = "distinctive-secret-token-value"
OPEN_FEATURES = {"type": "FeatureCollection", "features": [], "links": []}


class RecordingOpener:
    """Answers each request from ``answers`` (documents or exceptions) and records it."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[tuple[str, str, str | None, dict | None]] = []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data) if request.data else None
        self.requests.append((request.get_method(), request.full_url, request.get_header("Authorization"), body))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)


def provider(opener) -> PdcApiProvider:
    config = MontandonConfig(api_token=TOKEN, api_cache_path=Path("unused-cache"))
    return PdcApiProvider(config, opener=opener, sleeper=lambda _seconds: None)


class RequestJsonTests(unittest.TestCase):
    def test_post_search_sends_the_bearer_token_in_a_header_only(self) -> None:
        opener = RecordingOpener({"features": [{"id": "a"}, {"id": "b"}], "links": []})
        body = {"collections": ["pdc-events"], "limit": 2}
        document = provider(opener).request_json(f"{BASE}/search", method="post", body=body)
        self.assertEqual([item["id"] for item in document["features"]], ["a", "b"])
        self.assertEqual(opener.requests, [("POST", f"{BASE}/search", f"Bearer {TOKEN}", body)])

    def test_a_relative_link_is_resolved_against_the_api(self) -> None:
        opener = RecordingOpener(OPEN_FEATURES)
        provider(opener).request_json("search", method="POST", body={"limit": 1})
        self.assertEqual(opener.requests[0][:2], ("POST", f"{BASE}/search"))

    def test_only_read_only_requests_are_allowed(self) -> None:
        opener = RecordingOpener(OPEN_FEATURES)
        refused = (
            ("DELETE", f"{BASE}/search"),
            ("PUT", f"{BASE}/search"),
            ("POST", f"{BASE}/collections/pdc-events"),
            ("GET", f"{BASE}/collections"),
            ("GET", f"{BASE}/collections/pdc-events/items"),
            ("GET", f"{BASE}/collections/cems-hazards"),
            ("GET", f"{BASE}/collections/pdc-events/../../admin"),
            ("GET", "https://montandon-eoapi.ifrc.org/admin"),
            ("POST", f"{BASE}/search/../etl"),
        )
        for method, url in refused:
            with self.subTest(method=method, url=url), self.assertRaises(ApiError):
                provider(opener).request_json(url, method=method, body={} if method != "GET" else None)
        self.assertEqual(opener.requests, [])

    def test_metadata_requests_are_allowed(self) -> None:
        opener = RecordingOpener({"queryables": {}})
        for path in ("queryables", "collections/pdc-events", "collections/pdc-hazards/queryables", "collections/pdc-impacts"):
            self.assertEqual(provider(opener).request_json(path), {"queryables": {}})
        self.assertEqual([request[0] for request in opener.requests], ["GET"] * 4)

    def test_a_url_on_another_origin_never_receives_the_token(self) -> None:
        opener = RecordingOpener(OPEN_FEATURES)
        for url in ("https://evil.example/stac/search", "http://montandon-eoapi.ifrc.org/stac/search", "https://montandon-eoapi.ifrc.org:8443/stac/search"):
            with self.subTest(url=url), self.assertRaises(ApiError):
                provider(opener).request_json(url, method="POST", body={})
        self.assertEqual(opener.requests, [])

    def test_transient_failures_are_retried_counted_and_bounded(self) -> None:
        stats: dict[str, int] = {}
        opener = RecordingOpener(http_error(503), http_error(502), OPEN_FEATURES)
        document = provider(opener).request_json(f"{BASE}/search", method="POST", body={}, stats=stats)
        self.assertEqual(document, OPEN_FEATURES)
        self.assertEqual(stats, {"retries": 2})

        opener = RecordingOpener(http_error(500))
        stats = {}
        with self.assertRaises(ApiError) as caught:
            provider(opener).request_json(f"{BASE}/search", method="POST", body={}, stats=stats)
        self.assertEqual(len(opener.requests), 4)  # one request and three bounded retries
        self.assertEqual(stats["retries"], 3)
        self.assertTrue(caught.exception.recoverable)
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_authentication_failures_are_not_retried_and_not_recoverable(self) -> None:
        opener = RecordingOpener(http_error(401))
        with self.assertRaises(ApiError) as caught:
            provider(opener).request_json(f"{BASE}/search", method="POST", body={})
        self.assertEqual(len(opener.requests), 1)
        self.assertFalse(caught.exception.recoverable)
        self.assertEqual(caught.exception.status, 401)
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_a_redirect_is_surfaced_and_not_followed(self) -> None:
        opener = RecordingOpener(http_error(302))
        with self.assertRaises(ApiError) as caught:
            provider(opener).request_json(f"{BASE}/search", method="POST", body={})
        self.assertEqual(len(opener.requests), 1)
        self.assertFalse(caught.exception.recoverable)


class FindTokenTests(unittest.TestCase):
    def test_environment_variable_wins_over_a_env_file(self) -> None:
        with TemporaryDirectory() as folder:
            (Path(folder) / ".env").write_text(f"{TOKEN_VARIABLE}=from-file\n", encoding="utf-8")
            with patch.dict(os.environ, {TOKEN_VARIABLE: " from-environment "}):
                token, source = find_token(Path(folder))
        self.assertEqual(token, "from-environment")
        self.assertIn(TOKEN_VARIABLE, source)

    def test_env_file_in_a_parent_folder_is_found_with_quotes_and_export(self) -> None:
        with TemporaryDirectory() as folder, patch.dict(os.environ, {TOKEN_VARIABLE: ""}):
            root = Path(folder)
            (root / ".env").write_text(f"# comment\nOTHER=1\nexport {TOKEN_VARIABLE}=\"quoted-value\"\n", encoding="utf-8")
            (root / "notebooks").mkdir()
            token, source = find_token(root / "notebooks")
        self.assertEqual(token, "quoted-value")
        self.assertEqual(source, "a .env file")

    def test_nothing_found_returns_none_and_search_is_bounded(self) -> None:
        with TemporaryDirectory() as folder, patch.dict(os.environ, {TOKEN_VARIABLE: ""}):
            root = Path(folder)
            (root / ".env").write_text(f"{TOKEN_VARIABLE}=too-far-away\n", encoding="utf-8")
            deep = root / "a" / "b" / "c" / "d"
            deep.mkdir(parents=True)
            (root / "a" / ".env").write_text(f"{TOKEN_VARIABLE}=\n", encoding="utf-8")  # empty value is ignored
            self.assertEqual(find_token(deep), (None, None))


if __name__ == "__main__":
    unittest.main()
