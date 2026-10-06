"""Bounded, read-only Montandon STAC API provider."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import http.client
import json
from pathlib import Path
import re
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urljoin, urlparse, urlencode, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import MontandonConfig
from .models import QuerySpec, RetrievalMetadata, ValidationError
from .taxonomy import KNOWN_CATEGORIES, KNOWN_IMPACT_TYPES, TYPE_CATEGORIES


COLLECTIONS = ("pdc-events", "pdc-hazards", "pdc-impacts")
# The only GET paths (relative to the API root) a caller may request: metadata
# and queryables of the three PDC collections. Anything else is refused.
READ_ONLY_GET = re.compile(r"queryables|collections/pdc-(?:events|hazards|impacts)(?:/queryables)?")
# Retried in place with bounded backoff. 500 is included because large
# production searches can fail transiently; persistent failures adapt below.
TRANSIENT_HTTP = {429, 500, 502, 503, 504}
# Request-shape rejections that a smaller request may avoid.
ADAPTABLE_HTTP = {400, 413, 414}
# Production honours limits of at least 1,000 (Phase 0 probe, 2026-10-01);
# 250 reduced a one-year impact read from 109 to 44 pages.
DEFAULT_PAGE_LIMIT = 250
PAGE_LIMIT_LADDER = (250, 100, 50)
MIN_BISECT_WINDOW = timedelta(days=1)
DEFAULT_FAILURE_BUDGET = 150
SENSITIVE_QUERY_PARTS = ("token", "secret", "signature", "credential", "accesskey", "authorization")
DEFAULT_FIELDS = (
    "id",
    "collection",
    "geometry",
    "bbox",
    "assets",
    "links",
    "properties.datetime",
    "properties.start_datetime",
    "properties.end_datetime",
    "properties.title",
    "properties.description",
    "properties.roles",
    "properties.monty:src_event_id",
    "properties.monty:corr_id",
    "properties.monty:episode_number",
    "properties.monty:country_codes",
    "properties.monty:hazard_codes",
    "properties.monty:hazard_detail",
    "properties.monty:impact_detail",
    "properties.processing:version",
)


class _RefuseRedirects(HTTPRedirectHandler):
    """Surface every redirect as an HTTPError instead of following it.

    urllib copies request headers, including ``Authorization``, onto the
    redirected request, so following a redirect could send the bearer token
    to another host. Allow-listed asset hosts must not forward requests either.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002 - urllib signature
        return None


def open_without_redirects(request: Request, timeout: float | None = None):
    """``urlopen`` equivalent that never follows redirects (3xx raises HTTPError)."""

    return build_opener(_RefuseRedirects).open(request, timeout=timeout)


class ApiError(RuntimeError):
    """A safe API failure without response bodies or credentials.

    ``recoverable`` marks failures a smaller or later request may avoid
    (transient/overload statuses, request-shape rejections, transport errors).
    Authentication, origin, and malformed-response failures are never
    recoverable. ``page_number`` is the page that failed, when known.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        path: str | None = None,
        recoverable: bool = False,
        page_number: int | None = None,
    ):
        self.status = status
        self.path = path
        self.recoverable = recoverable
        self.page_number = page_number
        super().__init__(message)


class PartitionTooLargeError(ApiError):
    """A one-day partition still exceeds the bounded record cap."""


@dataclass(frozen=True, slots=True)
class PageSummary:
    page: int
    returned: int
    next_present: bool
    server_count: int | None = None


@dataclass(frozen=True, slots=True)
class PartitionResult:
    collection: str
    start: str
    end: str
    request: Mapping[str, object]
    items: tuple[dict, ...]
    pages: tuple[PageSummary, ...]
    returned_count: int
    unique_count: int
    duplicate_ids: tuple[str, ...]
    complete: bool
    stop_reason: str
    cache_key: str
    retrieved_at: str
    query_fingerprint: str
    from_cache: bool = False
    limit: int = DEFAULT_PAGE_LIMIT
    filter_part: str | None = None
    adaptations: tuple[str, ...] = ()
    failure: str | None = None
    status: int | None = None

    def log_entry(self) -> dict[str, Any]:
        """Sanitised one-line record for manifests and the Quality view."""

        return {
            "collection": self.collection,
            "start": self.start,
            "end": self.end,
            "limit": self.limit,
            "filter_part": self.filter_part,
            "pages": len(self.pages),
            "items": self.unique_count,
            "complete": self.complete,
            "adaptations": list(self.adaptations),
            "failure": self.failure,
            "status": self.status,
        }

    def failure_label(self) -> str:
        part = f" [{self.filter_part}]" if self.filter_part else ""
        return f"{self.collection} {self.start[:10]}→{self.end[:10]}{part}: {self.failure or self.stop_reason}"


@dataclass(frozen=True, slots=True)
class PartitionEvent:
    """Progress for one top-level retrieval window, emitted in the caller thread."""

    collection: str
    start: str
    end: str
    completed_windows: int
    total_windows: int
    items: int
    complete: bool
    adapted: bool


@dataclass(frozen=True, slots=True)
class ApiQueryResult:
    collection: str
    items: tuple[dict, ...]
    partitions: tuple[PartitionResult, ...]
    returned_count: int
    unique_count: int
    duplicate_ids: tuple[str, ...]
    complete: bool
    query_fingerprint: str
    warnings: tuple[str, ...] = ()
    from_cache: bool = False
    window_results: Mapping[tuple[str, str], tuple[PartitionResult, ...]] = field(default_factory=dict)

    @property
    def failures(self) -> tuple[PartitionResult, ...]:
        return tuple(partition for partition in self.partitions if partition.failure is not None)

    def metadata(self, query: QuerySpec, endpoint: str) -> RetrievalMetadata:
        retrieved_at = max(
            (partition.retrieved_at for partition in self.partitions),
            default=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        adapted = sum(
            bool(ERROR_ADAPTATIONS.intersection(partition.adaptations)) and partition.failure is None
            for partition in self.partitions
        )
        warnings = list(self.warnings)
        if adapted:
            warnings.append(f"{self.collection}: {adapted} partition(s) completed after API errors by retrying smaller requests.")
        if self.failures:
            warnings.append(f"{self.collection}: {len(self.failures)} partition(s) could not be retrieved; the result is incomplete.")
        return RetrievalMetadata(
            provider="api",
            endpoint_or_file=endpoint,
            retrieved_at=retrieved_at,
            query_fingerprint=self.query_fingerprint,
            pages=sum(len(partition.pages) for partition in self.partitions),
            returned_count=self.returned_count,
            unique_count=self.unique_count,
            complete=self.complete,
            warnings=tuple(warnings),
            failed_partitions=tuple(partition.failure_label() for partition in self.failures),
            partition_log=tuple(partition.log_entry() for partition in self.partitions),
        )


@dataclass(frozen=True, slots=True)
class IdBatchResult:
    collection: str
    requested_ids: tuple[str, ...]
    items: tuple[dict, ...]
    returned_ids: tuple[str, ...]
    missing_ids: tuple[str, ...]
    duplicate_ids: tuple[str, ...]
    batches: int
    pages: tuple[PageSummary, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class CapabilityProfile:
    root_queryables: Mapping[str, object]
    collections: Mapping[str, Mapping[str, object]]
    queryables: Mapping[str, Mapping[str, object]]
    retrieved_at: str
    from_cache: bool = False


def _safe_path(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path or "/"


def _retry_delay(error: HTTPError, attempt: int) -> float:
    """Exponential backoff, or the server's Retry-After seconds (capped)."""

    value = error.headers.get("Retry-After") if error.headers is not None else None
    try:
        seconds = float(value) if value is not None else None
    except ValueError:
        seconds = None
    return min(seconds, 30.0) if seconds is not None and seconds >= 0 else float(2**attempt)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


# Adaptations taken because the API failed (as opposed to the routine cap split
# or reuse of a completed window during "retry failed parts").
ERROR_ADAPTATIONS = frozenset({"bisect_window", "smaller_pages", "split_filter"})


def _part_label(part: tuple[tuple[str, ...], tuple[str, ...]] | None) -> str | None:
    if part is None:
        return None
    types, categories = part
    return f"{'+'.join(types)}:{'+'.join(categories)}"


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80 if parsed.scheme.lower() == "http" else None)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


def _redact_sensitive_urls(value: object) -> object:
    if isinstance(value, dict):
        return {key: _redact_sensitive_urls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_sensitive_urls(item) for item in value]
    if not isinstance(value, str) or "://" not in value:
        return value
    parsed = urlparse(value)
    if not parsed.query:
        return value
    query = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        query.append((key, "<redacted>" if any(part in lowered for part in SENSITIVE_QUERY_PARTS) else item))
    return urlunparse(parsed._replace(query=urlencode(query)))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(_redact_sensitive_urls(value), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _equality(property_name: str, value: str) -> dict[str, object]:
    return {"op": "=", "args": [{"property": property_name}, value]}


def _array_filter(op: str, property_name: str, values: Iterable[str]) -> dict[str, object] | None:
    values = tuple(values)
    if not values:
        return None
    return {"op": op, "args": [{"property": property_name}, list(values)]}


def build_cql_filter(
    *,
    start: str,
    end: str,
    country: str | None = None,
    hazard_codes: Iterable[str] = (),
    impact_types: Iterable[str] = (),
    categories: Iterable[str] = (),
) -> dict[str, object]:
    """Build the scalar CQL2 filter shared by API queries and profiling."""

    args: list[dict[str, object]] = [
        {"op": ">=", "args": [{"property": "datetime"}, start]},
        {"op": "<", "args": [{"property": "datetime"}, end]},
    ]
    if country:
        args.append({"op": "a_contains", "args": [{"property": "monty:country_codes"}, country]})
    hazard_clause = _array_filter("a_overlaps", "monty:hazard_codes", hazard_codes)
    if hazard_clause:
        args.append(hazard_clause)
    for property_name, values in (
        ("monty:impact_detail.type", tuple(impact_types)),
        ("monty:impact_detail.category", tuple(categories)),
    ):
        if len(values) == 1:
            args.append(_equality(property_name, values[0]))
        elif values:
            args.append({"op": "or", "args": [_equality(property_name, value) for value in values]})
    return {"op": "and", "args": args}


def month_bounds(year: int, month: int) -> tuple[str, str]:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


def build_search_body(
    query: QuerySpec,
    collection: str,
    *,
    start: str,
    end: str,
    limit: int = DEFAULT_PAGE_LIMIT,
    fields: Iterable[str] | None = DEFAULT_FIELDS,
    impact_types: tuple[str, ...] | None = None,
    categories: tuple[str, ...] | None = None,
) -> dict[str, object]:
    """Build one bounded search body.

    ``impact_types``/``categories`` narrow an impact search to one part of the
    query (used when a large request is split). A clause that would list every
    known value is omitted, which returns the same items with a smaller body.
    """

    if collection not in COLLECTIONS:
        raise ValueError(f"unsupported PDC collection: {collection}")
    if limit < 1:
        raise ValueError("limit must be positive")
    is_impact = collection == "pdc-impacts"
    if is_impact and not query.retrieves_impact_detail:
        raise ValidationError(("impact-detail retrieval requires country_detail",))
    types = tuple(query.impact_types if impact_types is None else impact_types) if is_impact else ()
    selected = tuple(query.categories if categories is None else categories) if is_impact else ()
    if set(types) >= KNOWN_IMPACT_TYPES:
        types = ()
    if set(selected) >= KNOWN_CATEGORIES:
        selected = ()
    body: dict[str, object] = {
        "collections": [collection],
        "limit": limit,
        "filter-lang": "cql2-json",
        "filter": build_cql_filter(
            start=start,
            end=end,
            country=query.country_code,
            hazard_codes=query.hazard_codes,
            impact_types=types,
            categories=selected,
        ),
    }
    if fields is not None:
        body["fields"] = {"include": list(fields)}
    return body


class PdcApiProvider:
    """Read-only provider for bounded monthly PDC STAC searches."""

    def __init__(
        self,
        config: MontandonConfig,
        *,
        partition_cap: int = 25_000,
        max_pages: int | None = None,
        opener: Callable[..., object] = open_without_redirects,
        sleeper: Callable[[float], None] = time.sleep,
        max_workers: int | None = None,
        failure_budget: int = DEFAULT_FAILURE_BUDGET,
        fields: Iterable[str] | None = DEFAULT_FIELDS,
    ) -> None:
        if partition_cap < 1:
            raise ValueError("partition_cap must be positive")
        if max_pages is not None and max_pages < 1:
            raise ValueError("max_pages must be positive when supplied")
        workers = config.max_workers if max_workers is None else max_workers
        if workers < 1:
            raise ValueError("max_workers must be positive")
        self.config = config
        self.partition_cap = partition_cap
        self.max_pages = max_pages
        self.max_workers = workers
        self.failure_budget = failure_budget
        # Field projection for searches; a narrower projection (for example IDs
        # and dates only) is used for volume and coverage checks.
        self.fields = tuple(fields) if fields is not None else None
        self._opener = opener
        self._sleeper = sleeper
        self._failure_lock = threading.Lock()
        self._failed_requests = 0

    def _record_failure(self) -> bool:
        """Count one failed adaptive attempt; False once the budget is spent."""

        with self._failure_lock:
            self._failed_requests += 1
            return self._failed_requests <= self.failure_budget

    @property
    def failure_budget_spent(self) -> bool:
        with self._failure_lock:
            return self._failed_requests >= self.failure_budget

    def _request_json(
        self,
        url: str,
        *,
        method: str = "GET",
        body: Mapping[str, object] | None = None,
        stats: dict[str, int] | None = None,
    ) -> dict:
        if not self.config.api_token:
            raise ApiError("MONTANDON_API_TOKEN is required for API access", path=_safe_path(url))
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else None
        headers = {
            "Accept": "application/geo+json, application/json",
            "User-Agent": "guard-pdc-explorer-stage3/0.1",
            "Authorization": f"Bearer {self.config.api_token}",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"

        for attempt in range(4):
            request = Request(url, data=payload, headers=headers, method=method)
            try:
                with self._opener(request, timeout=self.config.timeout) as response:
                    data = json.loads(response.read())
                if not isinstance(data, dict):
                    raise ApiError("API returned a non-object JSON value", path=_safe_path(url))
                return data
            except HTTPError as error:
                if error.code in TRANSIENT_HTTP and attempt < 3:
                    if stats is not None:
                        stats["retries"] = stats.get("retries", 0) + 1
                    self._sleeper(_retry_delay(error, attempt))
                    continue
                raise ApiError(
                    f"{method} {_safe_path(url)} returned HTTP {error.code}",
                    status=error.code,
                    path=_safe_path(url),
                    recoverable=error.code in TRANSIENT_HTTP or error.code in ADAPTABLE_HTTP,
                ) from None
            except (TimeoutError, URLError, OSError, http.client.HTTPException) as error:
                # HTTPException covers a connection dropped mid-body (IncompleteRead).
                if attempt < 3:
                    if stats is not None:
                        stats["retries"] = stats.get("retries", 0) + 1
                    self._sleeper(2**attempt)
                    continue
                raise ApiError(
                    f"{method} {_safe_path(url)} failed with {type(error).__name__}",
                    path=_safe_path(url),
                    recoverable=True,
                ) from None
            except json.JSONDecodeError:
                raise ApiError(f"{method} {_safe_path(url)} returned invalid JSON", path=_safe_path(url)) from None

        raise ApiError(f"{method} {_safe_path(url)} failed after retries", path=_safe_path(url), recoverable=True)

    def request_json(
        self,
        url: str,
        *,
        method: str = "GET",
        body: Mapping[str, object] | None = None,
        stats: dict[str, int] | None = None,
    ) -> dict:
        """Send one read-only request and return its JSON document.

        This is the public single-request wrapper for callers that write their
        own retrieval loop (the notebook). It keeps the safety rules of the
        provider: transient failures are retried a bounded number of times,
        redirects are refused, errors never contain credentials, and the bearer
        token is only sent to the configured API origin. Only the read-only
        requests this project needs are allowed: ``GET`` of the queryables and
        the three PDC collections, and ``search`` (``POST``, or the ``GET``
        form of a continuation link). ``stats["retries"]`` counts retried attempts.
        """

        method = str(method).upper()
        absolute = urljoin(f"{self.config.endpoint}/", url)
        if _origin(absolute) != _origin(self.config.endpoint):
            raise ApiError("request points outside the configured API origin", path=_safe_path(absolute))
        base = urlparse(self.config.endpoint).path.rstrip("/")
        path = urlparse(absolute).path
        relative = path[len(base):].strip("/") if path.startswith(f"{base}/") else None
        allowed = (
            relative == "search" and method in {"GET", "POST"}
            or method == "GET" and relative is not None and READ_ONLY_GET.fullmatch(relative) is not None
        )
        if not allowed:
            raise ApiError(f"{method} {_safe_path(absolute)} is not an allowed read-only request", path=_safe_path(absolute))
        return self._request_json(absolute, method=method, body=body, stats=stats)

    def discover(self, collections: Iterable[str] = COLLECTIONS, *, refresh: bool = False) -> CapabilityProfile:
        selected = tuple(collections)
        invalid = sorted(set(selected).difference(COLLECTIONS))
        if invalid:
            raise ValueError(f"unsupported PDC collections: {', '.join(invalid)}")

        cache_name = sha256(
            json.dumps(
                {"endpoint": self.config.endpoint, "collections": selected},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        cache_path = self.config.api_cache_path / "capabilities" / f"{cache_name}.json"
        if not refresh and cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("complete"):
                return CapabilityProfile(
                    root_queryables=cached["root_queryables"],
                    collections=cached["collections"],
                    queryables=cached["queryables"],
                    retrieved_at=cached["retrieved_at"],
                    from_cache=True,
                )

        root_queryables = self._request_json(self.config.url("queryables"))
        metadata: dict[str, Mapping[str, object]] = {}
        queryables: dict[str, Mapping[str, object]] = {}
        for collection in selected:
            metadata[collection] = self._request_json(self.config.url(f"collections/{collection}"))
            queryables[collection] = self._request_json(self.config.url(f"collections/{collection}/queryables"))
        retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _write_json(
            cache_path,
            {
                "complete": True,
                "endpoint": self.config.endpoint,
                "collections_requested": list(selected),
                "retrieved_at": retrieved_at,
                "root_queryables": root_queryables,
                "collections": metadata,
                "queryables": queryables,
            },
        )
        return CapabilityProfile(root_queryables, metadata, queryables, retrieved_at)

    def _cache_key(self, query: QuerySpec | None, collection: str, body: Mapping[str, object]) -> str:
        payload = {
            "endpoint": self.config.endpoint,
            "collection": collection,
            "query": query.retrieval_dict() if query else None,
            "request": body,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode("utf-8")).hexdigest()

    def _cache_dir(self, cache_key: str) -> Path:
        return self.config.api_cache_path / cache_key

    def _next_link(self, data: Mapping[str, object]) -> Mapping[str, object] | None:
        links = data.get("links")
        if not isinstance(links, list):
            return None
        return next((link for link in links if isinstance(link, dict) and link.get("rel") == "next"), None)

    def _fetch_documents(
        self,
        *,
        url: str,
        method: str,
        body: Mapping[str, object] | None,
        item_cap: int | None,
    ) -> tuple[list[dict], tuple[PageSummary, ...], int, tuple[str, ...], bool, str, list[dict]]:
        documents: list[dict] = []
        items_by_id: dict[str, dict] = {}
        duplicate_ids: list[str] = []
        pages: list[PageSummary] = []
        returned_count = 0
        complete = False
        stop_reason = "pagination_exhausted"
        seen_requests: set[tuple[str, str, str]] = set()
        page_number = 0

        while True:
            page_number += 1
            if self.max_pages is not None and page_number > self.max_pages:
                complete = False
                stop_reason = "page_cap"
                break
            if method == "POST" and body is None:
                stop_reason = "next_post_link_missing_body"
                break
            request_key = (method, url, json.dumps(body, sort_keys=True, separators=(",", ":")))
            if request_key in seen_requests:
                stop_reason = "pagination_loop"
                break
            seen_requests.add(request_key)
            try:
                data = self._request_json(url, method=method, body=body)
            except ApiError as error:
                error.page_number = page_number
                raise
            documents.append(data)
            features = data.get("features")
            if not isinstance(features, list):
                raise ApiError(f"{method} {_safe_path(url)} returned a non-list features value", path=_safe_path(url))
            returned_count += len(features)
            for offset, item in enumerate(features):
                if not isinstance(item, dict):
                    raise ApiError(f"{method} {_safe_path(url)} returned a non-object feature", path=_safe_path(url))
                item_id = item.get("id")
                key = str(item_id) if item_id is not None else f"__missing_id_{page_number}_{offset}"
                if key in items_by_id:
                    duplicate_ids.append(key)
                else:
                    items_by_id[key] = item

            link = self._next_link(data)
            server_count = data.get("numberMatched")
            pages.append(
                PageSummary(
                    page=page_number,
                    returned=len(features),
                    next_present=link is not None,
                    server_count=server_count if isinstance(server_count, int) else None,
                )
            )
            if item_cap is not None and (
                len(items_by_id) > item_cap or (len(items_by_id) >= item_cap and link is not None)
            ):
                complete = False
                stop_reason = "partition_cap"
                break
            if link is None:
                complete = True
                break
            href = link.get("href") if isinstance(link, dict) else None
            if not isinstance(href, str) or not href:
                stop_reason = "next_link_missing_href"
                break
            next_url = urljoin(url, href)
            if _origin(next_url) != _origin(self.config.endpoint):
                raise ApiError("pagination next link points outside the configured API origin", path=_safe_path(next_url))
            url = next_url
            method = str(link.get("method") or "GET").upper()
            body = link.get("body") if method == "POST" else None

        return list(items_by_id.values()), tuple(pages), returned_count, tuple(duplicate_ids), complete, stop_reason, documents

    def _partition_from_documents(
        self,
        *,
        collection: str,
        start: str,
        end: str,
        request: Mapping[str, object],
        cache_key: str,
        query_fingerprint: str,
        documents: list[dict],
        from_cache: bool,
    ) -> PartitionResult:
        items_by_id: dict[str, dict] = {}
        duplicate_ids: list[str] = []
        returned_count = 0
        pages: list[PageSummary] = []
        for page_number, document in enumerate(documents, start=1):
            features = document.get("features") or []
            returned_count += len(features)
            for offset, item in enumerate(features):
                key = str(item.get("id")) if item.get("id") is not None else f"__missing_id_{page_number}_{offset}"
                if key in items_by_id:
                    duplicate_ids.append(key)
                else:
                    items_by_id[key] = item
            link = self._next_link(document)
            server_count = document.get("numberMatched")
            pages.append(PageSummary(page_number, len(features), link is not None, server_count if isinstance(server_count, int) else None))
        manifest = self._cache_dir(cache_key) / "manifest.json"
        saved = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
        saved_pages = saved.get("pages")
        if isinstance(saved_pages, list):
            pages = [PageSummary(**summary) for summary in saved_pages]
        retrieved_at = saved.get("retrieved_at")
        if not isinstance(retrieved_at, str) or not retrieved_at:
            raise ApiError("completed cache manifest is missing retrieved_at")
        return PartitionResult(
            collection=collection,
            start=start,
            end=end,
            request=request,
            items=tuple(items_by_id.values()),
            pages=tuple(pages),
            returned_count=saved.get("returned_count", returned_count),
            unique_count=saved.get("unique_count", len(items_by_id)),
            duplicate_ids=tuple(saved.get("duplicate_ids", duplicate_ids)),
            complete=True,
            stop_reason="pagination_exhausted",
            cache_key=cache_key,
            retrieved_at=retrieved_at,
            query_fingerprint=str(saved.get("query_fingerprint") or query_fingerprint),
            from_cache=from_cache,
        )

    def _load_cached_partition(
        self,
        *,
        collection: str,
        start: str,
        end: str,
        request: Mapping[str, object],
        cache_key: str,
        query_fingerprint: str,
    ) -> PartitionResult | None:
        cache_dir = self._cache_dir(cache_key)
        manifest_path = cache_dir / "manifest.json"
        if not manifest_path.exists():
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not manifest.get("complete"):
            return None
        # Read exactly the pages this manifest describes; a page file without
        # a manifest entry is never evidence of this retrieval.
        saved_pages = manifest.get("pages")
        if not isinstance(saved_pages, list) or not saved_pages:
            return None
        page_paths = [cache_dir / f"page_{number:03d}.json" for number in range(1, len(saved_pages) + 1)]
        if not all(path.exists() for path in page_paths):
            return None
        documents = [json.loads(path.read_text(encoding="utf-8")) for path in page_paths]
        return self._partition_from_documents(
            collection=collection,
            start=start,
            end=end,
            request=request,
            cache_key=cache_key,
            query_fingerprint=query_fingerprint,
            documents=documents,
            from_cache=True,
        )

    def _fetch_partition(
        self,
        *,
        query: QuerySpec,
        collection: str,
        start: str,
        end: str,
        limit: int = DEFAULT_PAGE_LIMIT,
        filter_part: tuple[tuple[str, ...], tuple[str, ...]] | None = None,
    ) -> PartitionResult:
        impact_types, categories = filter_part if filter_part is not None else (None, None)
        request = build_search_body(
            query, collection, start=start, end=end, limit=limit, fields=self.fields,
            impact_types=impact_types, categories=categories,
        )
        cache_key = self._cache_key(query, collection, request)
        if not query.refresh_api_cache:
            cached = self._load_cached_partition(
                collection=collection,
                start=start,
                end=end,
                request=request,
                cache_key=cache_key,
                query_fingerprint=query.fingerprint,
            )
            if cached:
                return cached

        documents_result = self._fetch_documents(
            url=self.config.url("search"),
            method="POST",
            body=request,
            item_cap=self.partition_cap,
        )
        items, pages, returned_count, duplicate_ids, complete, stop_reason, documents = documents_result
        retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if complete:
            cache_dir = self._cache_dir(cache_key)
            # Invalidate first, then drop pages of an older, longer retrieval so
            # they can never be merged into this one.
            (cache_dir / "manifest.json").unlink(missing_ok=True)
            for stale in cache_dir.glob("page_*.json"):
                stale.unlink()
            for page_number, document in enumerate(documents, start=1):
                _write_json(cache_dir / f"page_{page_number:03d}.json", document)
            _write_json(
                cache_dir / "manifest.json",
                {
                    "collection": collection,
                    "request": request,
                    "start": start,
                    "end": end,
                    "pages": [asdict(summary) for summary in pages],
                    "returned_count": returned_count,
                    "unique_count": len(items),
                    "duplicate_ids": list(duplicate_ids),
                    "complete": True,
                    "stop_reason": stop_reason,
                    "retrieved_at": retrieved_at,
                    "query_fingerprint": query.fingerprint,
                },
            )
        return PartitionResult(
            collection=collection,
            start=start,
            end=end,
            request=request,
            items=tuple(items),
            pages=pages,
            returned_count=returned_count,
            unique_count=len(items),
            duplicate_ids=duplicate_ids,
            complete=complete,
            stop_reason=stop_reason,
            cache_key=cache_key,
            retrieved_at=retrieved_at,
            query_fingerprint=query.fingerprint,
            limit=limit,
            filter_part=_part_label(filter_part),
        )

    def _failed_partition(
        self,
        *,
        query: QuerySpec,
        collection: str,
        start: datetime,
        end: datetime,
        limit: int,
        filter_part: tuple[tuple[str, ...], tuple[str, ...]] | None,
        adaptations: tuple[str, ...],
        failure: str,
        status: int | None,
    ) -> PartitionResult:
        return PartitionResult(
            collection=collection,
            start=_iso(start),
            end=_iso(end),
            request={},
            items=(),
            pages=(),
            returned_count=0,
            unique_count=0,
            duplicate_ids=(),
            complete=False,
            stop_reason="failed",
            cache_key="",
            retrieved_at=_iso(datetime.now(timezone.utc)),
            query_fingerprint=query.fingerprint,
            limit=limit,
            filter_part=_part_label(filter_part),
            adaptations=adaptations,
            failure=failure,
            status=status,
        )

    def _impact_parts(self, query: QuerySpec) -> tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]:
        """One (type, category) filter per valid pair of the query's selection."""

        parts = []
        for impact_type in query.impact_types:
            allowed = TYPE_CATEGORIES.get(impact_type)
            for category in query.categories:
                if allowed is None or category in allowed:
                    parts.append(((impact_type,), (category,)))
        return tuple(parts)

    def _fetch_window(
        self,
        *,
        query: QuerySpec,
        collection: str,
        start: datetime,
        end: datetime,
        limit_index: int = 0,
        filter_part: tuple[tuple[str, ...], tuple[str, ...]] | None = None,
        adaptations: tuple[str, ...] = (),
    ) -> tuple[PartitionResult, ...]:
        """Retrieve one window, adapting to API failures without losing other work.

        Ladder for a recoverable failure (after in-place retries of transient
        statuses): bisect the time window when the failure follows successful
        pages or is an overload status, then shrink the page size, then split an
        impact filter into one request per type/category pair. A unit that still
        fails becomes an explicit failed partition; nothing is silently dropped.
        """

        limit = PAGE_LIMIT_LADDER[limit_index]
        if self.failure_budget_spent:
            return (
                self._failed_partition(
                    query=query, collection=collection, start=start, end=end, limit=limit, filter_part=filter_part,
                    adaptations=adaptations, failure="not attempted: API failure budget for this retrieval was spent", status=None,
                ),
            )
        try:
            result = self._fetch_partition(
                query=query, collection=collection, start=_iso(start), end=_iso(end), limit=limit, filter_part=filter_part,
            )
        except ApiError as error:
            if not error.recoverable:
                raise
            if not self._record_failure():
                return (
                    self._failed_partition(
                        query=query, collection=collection, start=start, end=end, limit=limit, filter_part=filter_part,
                        adaptations=adaptations, failure=f"{error}; API failure budget spent", status=error.status,
                    ),
                )
            deep = (error.page_number or 1) > 1
            overload = error.status is None or error.status in TRANSIENT_HTTP
            if (deep or overload) and end - start > MIN_BISECT_WINDOW:
                middle = start + (end - start) / 2
                middle = middle.replace(microsecond=0)
                return tuple(
                    partition
                    for child_start, child_end in ((start, middle), (middle, end))
                    for partition in self._fetch_window(
                        query=query, collection=collection, start=child_start, end=child_end,
                        limit_index=limit_index, filter_part=filter_part, adaptations=adaptations + ("bisect_window",),
                    )
                )
            if limit_index + 1 < len(PAGE_LIMIT_LADDER):
                return self._fetch_window(
                    query=query, collection=collection, start=start, end=end, limit_index=limit_index + 1,
                    filter_part=filter_part, adaptations=adaptations + ("smaller_pages",),
                )
            parts = self._impact_parts(query) if collection == "pdc-impacts" and filter_part is None else ()
            if len(parts) > 1:
                return tuple(
                    partition
                    for part in parts
                    for partition in self._fetch_window(
                        query=query, collection=collection, start=start, end=end, limit_index=limit_index,
                        filter_part=part, adaptations=adaptations + ("split_filter",),
                    )
                )
            return (
                self._failed_partition(
                    query=query, collection=collection, start=start, end=end, limit=limit, filter_part=filter_part,
                    adaptations=adaptations, failure=str(error), status=error.status,
                ),
            )
        result = replace(result, adaptations=adaptations)
        if result.complete:
            return (result,)
        if result.stop_reason != "partition_cap":
            # Incomplete pagination (loop or malformed next link) is surfaced as
            # an explicit failed partition, never as a partial window.
            return (
                self._failed_partition(
                    query=query, collection=collection, start=start, end=end, limit=limit, filter_part=filter_part,
                    adaptations=adaptations, failure=f"incomplete pagination: {result.stop_reason}", status=None,
                ),
            )
        duration = end - start
        if duration <= timedelta(days=1):
            raise PartitionTooLargeError(
                f"{collection} one-day partition exceeds the {self.partition_cap}-item cap"
            )
        step = timedelta(days=7 if duration > timedelta(days=7) else 1)
        partitions: list[PartitionResult] = []
        cursor = start
        while cursor < end:
            child_end = min(cursor + step, end)
            partitions.extend(
                self._fetch_window(
                    query=query, collection=collection, start=cursor, end=child_end, limit_index=limit_index,
                    filter_part=filter_part, adaptations=adaptations + ("cap_subdivision",),
                )
            )
            cursor = child_end
        return tuple(partitions)

    # Retained name for callers that predate the adaptive ladder.
    def _fetch_partition_with_subdivision(self, *, query: QuerySpec, collection: str, start: datetime, end: datetime) -> tuple[PartitionResult, ...]:
        return self._fetch_window(query=query, collection=collection, start=start, end=end)

    def query_collection(
        self,
        query: QuerySpec,
        collection: str,
        *,
        windows: Iterable[tuple[int, int]] | None = None,
        on_progress: Callable[[PartitionEvent], None] | None = None,
        reuse: Mapping[tuple[str, str], tuple[PartitionResult, ...]] | None = None,
    ) -> ApiQueryResult:
        """Retrieve every (year, month) window of the query.

        ``windows`` restricts retrieval to a subset of the query's windows (for
        example months that contain events). ``reuse`` supplies completed
        window results from an earlier attempt of the same retrieval so that
        "retry failed parts" re-requests only failed windows. Windows run on a
        bounded thread pool; progress is reported from the calling thread.
        """

        if not isinstance(query, QuerySpec):
            raise TypeError("query must be a QuerySpec")
        if collection not in COLLECTIONS:
            raise ValueError(f"unsupported PDC collection: {collection}")
        allowed = set(query.windows())
        selected = query.windows() if windows is None else tuple(window for window in query.windows() if window in set(windows))
        if windows is not None and not set(windows) <= allowed:
            raise ValueError("windows must belong to the query period")
        bounds = []
        for year, month in selected:
            start_text, end_text = month_bounds(year, month)
            bounds.append((start_text, end_text))

        window_results: dict[tuple[str, str], tuple[PartitionResult, ...]] = {}
        pending = []
        reused_marker = "reused_from_previous_attempt"
        for key in bounds:
            previous = (reuse or {}).get(key)
            if previous and all(partition.complete for partition in previous):
                window_results[key] = tuple(
                    partition if reused_marker in partition.adaptations
                    else replace(partition, adaptations=partition.adaptations + (reused_marker,))
                    for partition in previous
                )
            else:
                pending.append(key)

        def fetch(key: tuple[str, str]) -> tuple[PartitionResult, ...]:
            return self._fetch_window(query=query, collection=collection, start=_parse_iso(key[0]), end=_parse_iso(key[1]))

        def report(key: tuple[str, str], results: tuple[PartitionResult, ...]) -> None:
            if on_progress is not None:
                on_progress(
                    PartitionEvent(
                        collection=collection,
                        start=key[0],
                        end=key[1],
                        completed_windows=len(window_results),
                        total_windows=len(bounds),
                        items=sum(partition.unique_count for partition in results),
                        complete=all(partition.complete for partition in results),
                        adapted=any(ERROR_ADAPTATIONS.intersection(partition.adaptations) for partition in results),
                    )
                )

        for key, results in list(window_results.items()):
            report(key, results)
        if self.max_workers == 1 or len(pending) <= 1:
            for key in pending:
                window_results[key] = fetch(key)
                report(key, window_results[key])
        else:
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(pending)), thread_name_prefix="pdc-api") as pool:
                futures = {pool.submit(fetch, key): key for key in pending}
                try:
                    for future in as_completed(futures):
                        key = futures[future]
                        window_results[key] = future.result()
                        report(key, window_results[key])
                except BaseException:
                    # A fatal error (for example authentication) ends the
                    # retrieval: do not send the windows still queued.
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise

        # Deterministic merge order regardless of completion order.
        partitions: list[PartitionResult] = [
            partition for key in sorted(window_results) for partition in window_results[key]
        ]

        items_by_id: dict[str, dict] = {}
        duplicate_ids: list[str] = []
        returned_count = 0
        for partition in partitions:
            returned_count += partition.returned_count
            for item in partition.items:
                key = str(item.get("id")) if item.get("id") is not None else json.dumps(item, sort_keys=True)
                if key in items_by_id:
                    duplicate_ids.append(key)
                else:
                    items_by_id[key] = item
            duplicate_ids.extend(partition.duplicate_ids)
        return ApiQueryResult(
            collection=collection,
            items=tuple(items_by_id.values()),
            partitions=tuple(partitions),
            returned_count=returned_count,
            unique_count=len(items_by_id),
            duplicate_ids=tuple(dict.fromkeys(duplicate_ids)),
            complete=all(partition.complete for partition in partitions),
            query_fingerprint=query.fingerprint,
            warnings=query.warnings,
            from_cache=bool(partitions) and all(partition.from_cache for partition in partitions),
            window_results=window_results,
        )

    def years_with_events(self, years: Iterable[int], *, country: str | None = None) -> dict[int, bool]:
        """Which calendar years contain at least one PDC event item.

        One read-only ``limit=1`` search per year (optionally for one country),
        used only to offer selectable years in the interfaces. It is not
        evidence and never replaces a full retrieval.
        """

        selected = sorted(set(int(year) for year in years))

        def has_events(year: int) -> tuple[int, bool]:
            body = {
                "collections": ["pdc-events"],
                "limit": 1,
                "filter-lang": "cql2-json",
                "filter": build_cql_filter(start=f"{year}-01-01T00:00:00Z", end=f"{year + 1}-01-01T00:00:00Z", country=country),
                "fields": {"include": ["id"]},
            }
            features = self._request_json(self.config.url("search"), method="POST", body=body).get("features")
            return year, bool(features)

        if self.max_workers == 1 or len(selected) <= 1:
            return dict(has_events(year) for year in selected)
        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="pdc-years") as pool:
            try:
                return dict(pool.map(has_events, selected))
            except BaseException:
                pool.shutdown(wait=False, cancel_futures=True)
                raise

    def query_events(self, query: QuerySpec, **options: Any) -> ApiQueryResult:
        return self.query_collection(query, "pdc-events", **options)

    def query_hazards(self, query: QuerySpec, **options: Any) -> ApiQueryResult:
        return self.query_collection(query, "pdc-hazards", **options)

    def query_impacts(self, query: QuerySpec, **options: Any) -> ApiQueryResult:
        if not query.retrieves_impact_detail:
            raise ValidationError(("impact-detail retrieval requires country_detail",))
        return self.query_collection(query, "pdc-impacts", **options)

    def get_items_by_ids(self, collection: str, ids: Iterable[str], *, batch_size: int = 100) -> IdBatchResult:
        if collection not in COLLECTIONS:
            raise ValueError(f"unsupported PDC collection: {collection}")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        requested = tuple(dict.fromkeys(str(item) for item in ids))
        returned_by_id: dict[str, dict] = {}
        duplicate_ids: list[str] = []
        page_summaries: list[PageSummary] = []
        complete = True
        batch_count = 0
        for offset in range(0, len(requested), batch_size):
            batch_count += 1
            batch = requested[offset : offset + batch_size]
            body = {
                "collections": [collection],
                "ids": list(batch),
                "limit": len(batch),
                "fields": {"include": list(DEFAULT_FIELDS)},
            }
            items, pages, _, duplicates, batch_complete, reason, _ = self._fetch_documents(
                url=self.config.url("search"),
                method="POST",
                body=body,
                item_cap=None,
            )
            page_summaries.extend(pages)
            duplicate_ids.extend(duplicates)
            if not batch_complete:
                complete = False
                raise ApiError(f"ID batch {collection} incomplete: {reason}")
            for item in items:
                item_id = item.get("id")
                key = str(item_id) if item_id is not None else None
                if key in returned_by_id:
                    duplicate_ids.append(key)
                elif key is not None:
                    returned_by_id[key] = item
        returned_ids = tuple(item_id for item_id in requested if item_id in returned_by_id)
        return IdBatchResult(
            collection=collection,
            requested_ids=requested,
            items=tuple(returned_by_id[item_id] for item_id in returned_ids),
            returned_ids=returned_ids,
            missing_ids=tuple(item_id for item_id in requested if item_id not in returned_by_id),
            duplicate_ids=tuple(dict.fromkeys(duplicate_ids)),
            batches=batch_count,
            pages=tuple(page_summaries),
            complete=complete,
        )

    def get_item(self, collection: str, item_id: str) -> dict:
        if collection not in COLLECTIONS:
            raise ValueError(f"unsupported PDC collection: {collection}")
        if not item_id:
            raise ValueError("item_id must be non-empty")
        body = {"collections": [collection], "ids": [item_id], "limit": 1}
        items, _, _, _, complete, reason, _ = self._fetch_documents(
            url=self.config.url("search"),
            method="POST",
            body=body,
            item_cap=1,
        )
        if not complete:
            raise ApiError(f"full-item lookup incomplete: {reason}")
        matches = [
            item
            for item in items
            if str(item.get("id")) == item_id and item.get("collection") == collection
        ]
        if not matches:
            raise ApiError("requested item was not returned", status=404, path="/search")
        if len(matches) != 1:
            raise ApiError("full-item lookup returned duplicate matches", path="/search")
        return matches[0]
