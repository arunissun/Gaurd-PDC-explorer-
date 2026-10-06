"""An in-memory stand-in for the Montandon STAC ``/search`` endpoint (offline tests only).

It understands the small CQL2 subset this project sends (``and``, ``or``, ``=``,
``>=``, ``<``, ``a_contains``, ``a_overlaps``), ``ids``, ``fields`` projection,
``limit`` and POST continuation links, so a retrieval loop is exercised the way
it is against the live API. Nothing here touches the network.

``synthetic_pool`` builds PDC-shaped events, hazards and impacts (snapshots,
related links, shared multi-country events, a tsunami bulletin, a zero value,
a missing value, a conflicting value and an event without a source event ID).
"""

from __future__ import annotations

from bisect import bisect_left
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import gzip
import json
import os
from pathlib import Path
import threading
from typing import Any, Callable, Iterable, Iterator, Mapping
from unittest.mock import patch
from urllib.error import HTTPError
import uuid

API = "https://montandon-eoapi.ifrc.org/stac"
SEARCH_URL = f"{API}/search"
POOL_VARIABLE = "PDC_FAKE_STAC_POOL"
FAILURES_VARIABLE = "PDC_FAKE_STAC_FAILURES"


# ------------------------------------------------------------------ the server

def _time(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _property(item: Mapping[str, Any], name: str) -> Any:
    properties = item.get("properties") or {}
    if name == "id":
        return item.get("id")
    if name.startswith("monty:impact_detail."):
        key = name.split(".", 1)[1]
        detail = properties.get("monty:impact_detail")
        details = detail if isinstance(detail, list) else [detail] if isinstance(detail, dict) else []
        return [entry.get(key) for entry in details]
    return properties.get(name)


def matches(item: Mapping[str, Any], expression: Mapping[str, Any]) -> bool:
    """Evaluate the CQL2-JSON subset used by this project against one STAC item."""

    op, args = expression["op"], expression["args"]
    if op == "and":
        return all(matches(item, argument) for argument in args)
    if op == "or":
        return any(matches(item, argument) for argument in args)
    value, target = _property(item, args[0]["property"]), args[1]
    if op in {">=", "<", ">", "<="}:
        if value is None:
            return False
        left, right = _time(value), _time(target)
        return {">=": left >= right, "<": left < right, ">": left > right, "<=": left <= right}[op]
    if op == "=":
        return target in value if isinstance(value, list) else value == target
    if op == "a_contains":
        return isinstance(value, list) and target in value
    if op == "a_overlaps":
        return isinstance(value, list) and bool(set(value) & set(target))
    raise ValueError(f"unsupported CQL2 operator in the fake STAC server: {op}")


def _datetime_bounds(expression: Mapping[str, Any] | None) -> tuple[datetime | None, datetime | None]:
    low = high = None
    for clause in (expression or {}).get("args", []) if (expression or {}).get("op") == "and" else []:
        if clause.get("op") in {">=", "<"} and clause["args"][0] == {"property": "datetime"}:
            bound = _time(clause["args"][1])
            if clause["op"] == ">=":
                low = bound
            else:
                high = bound
    return low, high


def project(item: Mapping[str, Any], include: Iterable[str] | None) -> dict[str, Any]:
    """Apply a STAC ``fields.include`` list (top-level keys and ``properties.<name>``)."""

    if not include:
        return dict(item)
    result: dict[str, Any] = {}
    for path in include:
        head, _, tail = path.partition(".")
        if head not in item:
            continue
        if not tail:
            result[head] = item[head]
        elif tail in (item[head] or {}):
            result.setdefault(head, {})[tail] = item[head][tail]
    return result


class _Response:
    def __init__(self, document: Mapping[str, Any]):
        self._payload = json.dumps(document).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def read(self, _limit: int | None = None) -> bytes:
        return self._payload


class FakeStac:
    """A tiny read-only STAC search server. Call it like an opener: ``fake(request, timeout)``.

    ``failures`` are rules ``{"collection", "window_start", "min_offset", "status", "times"}``; a matching
    ``/search`` request answers with that HTTP status (``times`` limits how often; ``None`` = always).
    ``merge_links=True`` makes continuation links carry only the changed body fields plus ``merge: true``.
    """

    def __init__(self, items: Iterable[Mapping[str, Any]], *, failures: Iterable[Mapping[str, Any]] = (), merge_links: bool = False):
        by_collection: dict[str, list[Mapping[str, Any]]] = {}
        for item in items:
            by_collection.setdefault(item["collection"], []).append(item)
        self._items = {
            collection: sorted(values, key=lambda item: (_time(item["properties"]["datetime"]), item["id"]))
            for collection, values in by_collection.items()
        }
        self._times = {collection: [_time(item["properties"]["datetime"]) for item in values] for collection, values in self._items.items()}
        self.failures = [dict(rule, _used=0) for rule in failures]
        self.merge_links = merge_links
        self.requests: list[dict[str, Any]] = []
        self._matched: dict[str, list[Mapping[str, Any]]] = {}
        self._lock = threading.Lock()

    # --------------------------------------------------------------- matching
    def _candidates(self, collection: str, expression: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
        values = self._items.get(collection, [])
        low, high = _datetime_bounds(expression)
        times = self._times.get(collection, [])
        start = bisect_left(times, low) if low else 0
        stop = bisect_left(times, high) if high else len(values)
        return values[start:stop]

    def _search(self, body: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        key = json.dumps({key: body.get(key) for key in ("collections", "filter", "ids")}, sort_keys=True)
        with self._lock:
            cached = self._matched.get(key)
        if cached is not None:
            return cached
        found = []
        wanted_ids = set(body.get("ids") or ())
        for collection in body.get("collections") or list(self._items):
            for item in self._candidates(collection, body.get("filter")):
                if wanted_ids and item["id"] not in wanted_ids:
                    continue
                if body.get("filter") and not matches(item, body["filter"]):
                    continue
                found.append(item)
        # Newest first, like the API; ties broken by ID so paging is stable.
        found.sort(key=lambda item: (_time(item["properties"]["datetime"]), item["id"]), reverse=True)
        with self._lock:
            self._matched[key] = found
        return found

    # --------------------------------------------------------------- failures
    def _failure(self, body: Mapping[str, Any], offset: int) -> HTTPError | None:
        low, _ = _datetime_bounds(body.get("filter"))
        collection = (body.get("collections") or [None])[0]
        with self._lock:
            for rule in self.failures:
                if rule.get("collection") not in (None, collection):
                    continue
                if rule.get("window_start") and not (low and low.isoformat().startswith(rule["window_start"])):
                    continue
                if offset < rule.get("min_offset", 0):
                    continue
                if rule.get("times") is not None and rule["_used"] >= rule["times"]:
                    continue
                rule["_used"] += 1
                return HTTPError(SEARCH_URL, int(rule.get("status", 500)), "injected failure", {}, None)
        return None

    # ------------------------------------------------------------------ opener
    def __call__(self, request: Any, timeout: float | None = None) -> _Response:
        url, method = request.full_url, request.get_method()
        body = json.loads(request.data) if request.data else {}
        with self._lock:
            self.requests.append({"method": method, "url": url, "body": body})
        if not url.startswith(f"{API}/search") or method != "POST":
            return _Response({"queryables": {}})
        offset = int(str(body.get("token", "next:0")).rsplit(":", 1)[-1])
        failure = self._failure(body, offset)
        if failure is not None:
            raise failure
        matched = self._search(body)
        limit = int(body.get("limit", 10))
        page = matched[offset: offset + limit]
        links: list[dict[str, Any]] = []
        if offset + limit < len(matched):
            token = f"next:{offset + limit}"
            link: dict[str, Any] = {"rel": "next", "type": "application/geo+json", "method": "POST", "href": SEARCH_URL}
            link.update({"body": {"token": token}, "merge": True} if self.merge_links else {"body": {**body, "token": token}})
            links.append(link)
        include = (body.get("fields") or {}).get("include")
        return _Response({
            "type": "FeatureCollection",
            "features": [project(item, include) for item in page],
            "links": links,
            "numberReturned": len(page),
        })

    # ----------------------------------------------------------------- helpers
    def search_requests(self) -> list[dict[str, Any]]:
        return [request for request in self.requests if request["url"].startswith(f"{API}/search")]


@contextmanager
def installed(fake: FakeStac) -> Iterator[FakeStac]:
    """Route every request of ``guard_pdc.api`` to ``fake`` while the block runs."""

    class _Opener:
        def open(self, request: Any, timeout: float | None = None) -> _Response:
            return fake(request, timeout)

    with patch("guard_pdc.api.build_opener", lambda *_handlers: _Opener()):
        yield fake


def load_pool(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def dump_pool(items: Iterable[Mapping[str, Any]], path: str | os.PathLike[str]) -> None:
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "wt", encoding="utf-8") as handle:
        json.dump(list(items), handle)


def install_from_environment() -> FakeStac:
    """For a notebook kernel: serve ``$PDC_FAKE_STAC_POOL`` instead of the live API, for the kernel's lifetime."""

    failures = json.loads(os.environ.get(FAILURES_VARIABLE, "[]"))
    fake = FakeStac(load_pool(os.environ[POOL_VARIABLE]), failures=failures)

    class _Opener:
        def open(self, request: Any, timeout: float | None = None) -> _Response:
            return fake(request, timeout)

    patch("guard_pdc.api.build_opener", lambda *_handlers: _Opener()).start()
    return fake


# ------------------------------------------------------------ synthetic data

COUNTRY_CENTRES = {"PHL": (121.0, 14.0), "BGD": (90.4, 23.8), "NPL": (84.0, 28.0), "IND": (78.0, 22.0)}
COUNTRY_NAMES = {"PHL": "Philippines", "BGD": "Bangladesh", "NPL": "Nepal", "IND": "India"}
HAZARDS = (
    ("Flood", ("MH0600", "FL", "nat-hyd-flo-flo")),
    ("Tropical Cyclone", ("MH0306", "TC", "nat-met-sto-tro")),
    ("Landslide", ("GH0300", "LS", "nat-geo-mmd-lan")),
    ("Earthquake", ("GH0101", "EQ", "nat-geo-ear-gro")),
    ("Storm", ("MH0103", "ST", "nat-met-sto-sto")),
    ("Drought", ("MH0401", "DR", "nat-cli-dro-dro")),
    ("Wildfire", ("EN0205", "WF", "nat-cli-wil-wil")),
)
ALERTS = ("ADVISORY", "WATCH", "WARNING", "INFORMATION")
AGE_BANDS = (
    ("children_0_4", "total0_4", 0.09), ("children_5_9", "total5_9", 0.09), ("children_10_14", "total10_14", 0.09),
    ("children_15_19", "total15_19", 0.09), ("adult_20_24", "total20_24", 0.09), ("adult_25_29", "total25_29", 0.08),
    ("adult_30_34", "total30_34", 0.08), ("adult_35_39", "total35_39", 0.07), ("adult_40_44", "total40_44", 0.07),
    ("adult_45_49", "total45_49", 0.06), ("adult_50_54", "total50_54", 0.05), ("adult_55_59", "total55_59", 0.05),
    ("adult_60_64", "total60_64", 0.04), ("elderly", "total65_Plus", 0.05),
)


def _href(collection: str, item_id: str) -> str:
    return f"{API}/collections/{collection}/items/{item_id}"


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def synthetic_pool(countries: Iterable[str] = ("PHL", "BGD"), *, year: int = 2024, events: int = 12, with_age_bands: bool = True) -> list[dict[str, Any]]:
    """PDC-shaped items for ``countries``: ``events`` event families each, with 1-3 snapshots."""

    codes = tuple(countries)
    pool: list[dict[str, Any]] = []
    for position, country in enumerate(codes):
        for index in range(events):
            label, hazard_codes = HAZARDS[index % len(HAZARDS)]
            # Every sixth event is listed by the first two countries: one source event ID, one set of items.
            shared = len(codes) > 1 and position < 2 and index % 6 == 0
            if shared and position == 1:
                continue  # generated once, with the first country
            bulletin = index == 7
            legacy = index == 8
            if bulletin:
                label, hazard_codes = "Tsunami", ("MH0705", "TS", "nat-geo-ear-tsu")
            source_id = None if legacy else str(900_000 + (0 if shared else 1000 * position) + index)
            family = uuid.uuid5(uuid.NAMESPACE_URL, f"{source_id or 'legacy'}-{country if legacy else index}")
            month = (index * 5 + position) % 12 + 1
            start = datetime(year, month, 1 + (index * 3) % 27, 6, tzinfo=timezone.utc)
            end = start + timedelta(days=2)
            snapshots = 1 + index % 3
            lon, lat = COUNTRY_CENTRES.get(country, (0.0, 0.0))
            point = {"type": "Point", "coordinates": [round(lon + ((index * 37) % 61 - 30) / 10, 3), round(lat + ((index * 53) % 41 - 20) / 10, 3)]}
            title = f"{label} ({COUNTRY_NAMES.get(country, country)} Coast) - Region {index}" if bulletin else f"{label} - Region {index}, {COUNTRY_NAMES.get(country, country)}"
            corr_id = f"{start:%Y%m%d}-{country}-{index:06d}-{hazard_codes[0]}-1-GCDB"
            countries_listed = list(codes[:2]) if shared else [country]
            for snapshot in range(snapshots):
                exposure_time = start + timedelta(hours=6 * snapshot)
                epoch = int(exposure_time.timestamp())
                suffix = f"{family}-{source_id or 'legacy' + str(index)}-{epoch}"
                event_id, hazard_id = f"pdc-event-{suffix}", f"pdc-hazard-{suffix}"
                impacts: list[dict[str, Any]] = []
                people = 0.0 if index == 3 else float(round(10 ** (2.4 + ((index * 7 + position) % 40) / 10.0)))
                people *= 1 + 0.15 * snapshot
                kinds = [("people", "affected_total", "population-total", people)]
                if index not in (3, 4):
                    kinds += [
                        ("households", "affected_total", "population-households", round(people / 4)),
                        ("schools", "affected_total", "capital-school", float(index % 7)),
                        ("hospitals", "affected_total", "capital-hospital", float(index % 3)),
                        ("global_currency", "cost", "capital-total", round(people * 1_000.0)),
                    ]
                    if with_age_bands:
                        kinds += [(category, "affected_total", f"population-{band}", round(people * share)) for category, band, share in AGE_BANDS]
                if index == 4:
                    kinds = []  # an event with no exposure items at all: every measure is "not published"
                for impact_country in countries_listed:
                    for category, impact_type, kind, value in kinds:
                        impact_id = f"pdc-impact-{suffix}-1-{kind}-{impact_country}"
                        detail = {"type": impact_type, "category": category, "value": float(value), "estimate_type": "primary"}
                        impacts.append(_item(
                            "pdc-impacts", impact_id, point, start, end, title, source_id, corr_id, [impact_country], hazard_codes, snapshot + 1,
                            extra={"monty:impact_detail": detail, "roles": ["source", "impact"]},
                            links=[("pdc-events", event_id, "event"), ("pdc-hazards", hazard_id, "hazard")],
                        ))
                        if index == 6 and category == "people" and snapshot == 0 and impact_country == country:
                            # A second, different value for the same snapshot: the two must stay a visible conflict.
                            twin = dict(detail, value=float(value) * 2)
                            impacts.append(_item(
                                "pdc-impacts", f"pdc-impact-{suffix}-2-{kind}-{impact_country}", point, start, end, title, source_id, corr_id,
                                [impact_country], hazard_codes, snapshot + 1,
                                extra={"monty:impact_detail": twin, "roles": ["source", "impact"]},
                                links=[("pdc-events", event_id, "event"), ("pdc-hazards", hazard_id, "hazard")],
                            ))
                impact_links = [("pdc-impacts", impact["id"], "impact") for impact in impacts]
                description = f"{label} alert. It is estimated that {int(people):,} people are within the affected area(s). ${round(people / 1000) + 1} Million (USD) of infrastructure*" if index % 2 == 0 else f"{label} alert."
                event = _item(
                    "pdc-events", event_id, point, start, end, title, source_id, corr_id, countries_listed, hazard_codes, snapshot + 1,
                    extra={"roles": ["source", "event"], "description": description}, links=[("pdc-hazards", hazard_id, "hazard"), *impact_links],
                    assets=True, source_uuid=str(family),
                )
                hazard = _item(
                    "pdc-hazards", hazard_id, point, start, end, title, source_id, corr_id, countries_listed, hazard_codes[:1], snapshot + 1,
                    extra={"roles": ["source", "hazard"], "monty:hazard_detail": {
                        "estimate_type": "primary", "severity_unit": "PDC Severity Score",
                        "severity_label": ALERTS[(index + snapshot) % len(ALERTS)], "severity_value": 0.1}},
                    links=[("pdc-events", event_id, "event"), *impact_links], assets=True, source_uuid=str(family),
                )
                pool.extend([event, hazard, *impacts])
    assert len({item['id'] for item in pool}) == len(pool), 'synthetic item IDs must be unique'
    return pool


def _item(
    collection: str, item_id: str, geometry: Mapping[str, Any], start: datetime, end: datetime, title: str, source_id: str | None,
    corr_id: str, countries: list[str], hazard_codes: Iterable[str], episode: int, *, extra: Mapping[str, Any],
    links: Iterable[tuple[str, str, str]], assets: bool = False, source_uuid: str | None = None,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "datetime": _iso(start), "start_datetime": _iso(start), "end_datetime": _iso(end), "title": title,
        "monty:corr_id": corr_id, "monty:episode_number": episode, "monty:country_codes": list(countries),
        "monty:hazard_codes": list(hazard_codes), "processing:version": "0.2.7", **extra,
    }
    if source_id:
        properties["monty:src_event_id"] = source_id
    item: dict[str, Any] = {
        "type": "Feature", "stac_version": "1.0.0", "id": item_id, "collection": collection, "geometry": dict(geometry),
        "bbox": [*geometry["coordinates"], *geometry["coordinates"]], "properties": properties,
        "links": [{"rel": "related", "href": _href(target_collection, target_id), "type": "application/geo+json", "roles": [role]}
                  for target_collection, target_id, role in links],
    }
    if assets:
        item["assets"] = {
            "Maps": {"href": f"https://assets.example.invalid/maps/{item_id}.json?AWSAccessKeyId=x&Signature=y&Expires=1", "type": "geojson", "title": "Polygon"},
            "report": {"href": f"https://hazardbrief.pdc.org/PRODUCTION/ui/index.html?uuid={source_uuid}", "type": "html", "title": "Report"},
        }
    return item
