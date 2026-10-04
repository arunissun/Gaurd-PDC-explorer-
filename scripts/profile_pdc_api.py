"""Run small, read-only production PDC STAC capability and search probes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request

from guard_pdc.api import open_without_redirects


DEFAULT_ENDPOINT = "https://montandon-eoapi.ifrc.org/stac"
COLLECTIONS = ("pdc-events", "pdc-hazards", "pdc-impacts")
TRANSIENT_HTTP = {429, 502, 503, 504}
DEFAULT_FIELDS = [
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
]
SENSITIVE_QUERY_PARTS = ("token", "secret", "signature", "credential", "accesskey", "authorization")


class ProbeError(RuntimeError):
    """A bounded probe failed without exposing credentials or response bodies."""


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def token_from_env() -> str:
    value = os.environ.get("MONTANDON_API_TOKEN", "").strip()
    if not value or any(character.isspace() for character in value):
        raise ProbeError("Set MONTANDON_API_TOKEN through the local approved env setup.")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(redact_sensitive_urls(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def redact_sensitive_urls(value: object) -> object:
    """Preserve response structure while removing signed URL query values on disk."""
    if isinstance(value, dict):
        return {key: redact_sensitive_urls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_sensitive_urls(item) for item in value]
    if not isinstance(value, str) or "://" not in value:
        return value
    parsed = urlparse(value)
    if not parsed.query:
        return value
    redacted_query = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        redacted_query.append((key, "<redacted>" if any(part in lowered for part in SENSITIVE_QUERY_PARTS) else item))
    return urlunparse(parsed._replace(query=urlencode(redacted_query)))


def origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80 if parsed.scheme.lower() == "http" else None)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


def request_json(
    url: str,
    token: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    timeout: int = 45,
) -> dict:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {
        "Accept": "application/geo+json, application/json",
        "User-Agent": "guard-pdc-explorer-stage1/0.1",
        "Authorization": f"Bearer {token}",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"

    for attempt in range(4):
        try:
            request = Request(url, data=payload, headers=headers, method=method)
            with open_without_redirects(request, timeout=timeout) as response:
                data = json.load(response)
            if not isinstance(data, dict):
                raise ProbeError(f"{method} {urlparse(url).path} returned a non-object JSON value")
            return data
        except HTTPError as error:
            if error.code in TRANSIENT_HTTP and attempt < 3:
                time.sleep(2**attempt)
                continue
            raise ProbeError(f"{method} {urlparse(url).path} returned HTTP {error.code}") from None
        except (TimeoutError, URLError, OSError) as error:
            if attempt < 3:
                time.sleep(2**attempt)
                continue
            raise ProbeError(f"{method} {urlparse(url).path} failed with {type(error).__name__}") from None
        except json.JSONDecodeError:
            raise ProbeError(f"{method} {urlparse(url).path} returned invalid JSON") from None

    raise ProbeError(f"{method} {urlparse(url).path} failed after retries")


def request_asset(url: str, token: str, *, api_host: str, max_bytes: int, timeout: int) -> dict:
    parsed = urlparse(url)
    redacted = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    result = {
        "href": redacted,
        "query_present": bool(parsed.query),
        "host": parsed.netloc,
        "status": None,
        "content_type": None,
        "bytes_read": 0,
        "geojson_type": None,
        "geojson_features": None,
        "schema_valid": None,
    }
    if parsed.scheme not in {"http", "https"}:
        result["status"] = "unsupported_scheme"
        return result
    if parsed.netloc != api_host:
        result["status"] = "not_fetched_external_host"
        return result

    request = Request(
        url,
        headers={
            "Accept": "application/geo+json, application/json, text/html, */*",
            "User-Agent": "guard-pdc-explorer-stage1/0.1",
            "Authorization": f"Bearer {token}",
        },
        method="GET",
    )
    try:
        with open_without_redirects(request, timeout=timeout) as response:
            result["status"] = response.status
            result["content_type"] = response.headers.get("Content-Type")
            content = response.read(max_bytes + 1)
    except HTTPError as error:
        result["status"] = f"http_{error.code}"
        return result
    except (TimeoutError, URLError, OSError) as error:
        result["status"] = type(error).__name__
        return result

    result["bytes_read"] = len(content)
    if len(content) > max_bytes:
        result["status"] = "oversized"
        return result
    if "json" not in (result["content_type"] or "").lower() and not content.lstrip().startswith(b"{"):
        result["status"] = "accessible_non_json"
        return result
    try:
        document = json.loads(content)
    except json.JSONDecodeError:
        result["status"] = "invalid_json"
        return result
    result["geojson_type"] = document.get("type") if isinstance(document, dict) else None
    features = document.get("features") if isinstance(document, dict) else None
    result["geojson_features"] = len(features) if isinstance(features, list) else None
    result["schema_valid"] = isinstance(document, dict) and document.get("type") in {
        "Feature",
        "FeatureCollection",
        "GeometryCollection",
    }
    result["status"] = "accessible"
    return result


def cql_property(name: str) -> dict:
    return {"property": name}


def equality(name: str, value: str) -> dict:
    return {"op": "=", "args": [cql_property(name), value]}


def build_filter(
    *,
    start: str,
    end: str,
    country: str | None,
    hazards: list[str],
    impact_type: str | None = None,
    categories: list[str] | None = None,
) -> dict:
    args = [
        {"op": ">=", "args": [cql_property("datetime"), start]},
        {"op": "<", "args": [cql_property("datetime"), end]},
    ]
    if country:
        args.append({"op": "a_contains", "args": [cql_property("monty:country_codes"), country]})
    if hazards:
        args.append({"op": "a_overlaps", "args": [cql_property("monty:hazard_codes"), hazards]})
    if impact_type:
        args.append(equality("monty:impact_detail.type", impact_type))
    if categories:
        clauses = [equality("monty:impact_detail.category", category) for category in categories]
        args.append(clauses[0] if len(clauses) == 1 else {"op": "or", "args": clauses})
    return {"op": "and", "args": args}


def next_link(data: dict) -> dict | None:
    return next(
        (link for link in data.get("links", []) if isinstance(link, dict) and link.get("rel") == "next"),
        None,
    )


def fetch_search(
    endpoint: str,
    token: str,
    body: dict,
    *,
    label: str,
    raw_dir: Path,
    max_pages: int,
    max_items: int,
    timeout: int,
) -> dict:
    url = endpoint + "/search"
    method = "POST"
    payload: dict | None = body
    seen_next: set[str] = set()
    features: list[dict] = []
    pages: list[dict] = []
    pagination_exhausted = False
    stop_reason = None

    for page_number in range(1, max_pages + 1):
        if method == "POST" and payload is None:
            stop_reason = "next_post_link_missing_body"
            break
        data = request_json(url, token, method=method, body=payload, timeout=timeout)
        write_json(raw_dir / f"{label}_page_{page_number:03d}.json", data)
        page_features = data.get("features") or []
        if not isinstance(page_features, list):
            raise ProbeError(f"{label} page {page_number} has a non-list features value")
        features.extend(feature for feature in page_features if isinstance(feature, dict))
        pages.append(
            {
                "page": page_number,
                "returned": len(page_features),
                "number_matched": data.get("numberMatched"),
                "next_present": next_link(data) is not None,
            }
        )
        link = next_link(data)
        if not link:
            pagination_exhausted = True
            break
        if len(features) >= max_items:
            stop_reason = "profile_item_cap"
            break
        href = link.get("href")
        if not href:
            stop_reason = "next_link_missing_href"
            break
        next_url = urljoin(url, href)
        if origin(next_url) != origin(endpoint):
            raise ProbeError("Pagination next link points outside the configured API origin")
        url = next_url
        if url in seen_next:
            stop_reason = "pagination_loop"
            break
        seen_next.add(url)
        method = str(link.get("method") or "GET").upper()
        payload = link.get("body") if method == "POST" else None
        if method == "POST" and not isinstance(payload, dict):
            stop_reason = "next_post_link_missing_body"
            break
    else:
        stop_reason = "profile_page_cap"

    return {
        "request": body,
        "request_fingerprint": hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "pages": pages,
        "returned_raw": sum(page["returned"] for page in pages),
        "items_used_for_profile": min(len(features), max_items),
        "items": features[:max_items],
        "pagination_exhausted": pagination_exhausted,
        "stop_reason": stop_reason,
    }


def counter_dict(value: Counter | dict) -> dict:
    if isinstance(value, Counter):
        return {str(key): count for key, count in sorted(value.items(), key=lambda item: str(item[0]))}
    return {str(key): counter_dict(item) if isinstance(item, (Counter, dict)) else item for key, item in value.items()}


def inspect_features(features: list[dict]) -> tuple[dict, list[dict]]:
    collections = Counter()
    geometry_types = Counter()
    date_values: dict[str, list[str]] = defaultdict(list)
    categories: dict[str, Counter] = defaultdict(Counter)
    source_coverage: dict[str, Counter] = defaultdict(Counter)
    source_geometry: dict[str, set[str]] = defaultdict(set)
    assets = []
    property_names = Counter()
    top_level_names = Counter()

    for item in features:
        collection = str(item.get("collection") or "unknown")
        collections[collection] += 1
        for key in item:
            top_level_names[key] += 1
        geometry = item.get("geometry") or {}
        geometry_types[str(geometry.get("type") or "missing")] += 1
        properties = item.get("properties") or {}
        for key in properties:
            property_names[key] += 1
        source_id = properties.get("monty:src_event_id")
        corr_id = properties.get("monty:corr_id")
        coverage = source_coverage[collection]
        coverage["items"] += 1
        coverage["with_src_event_id"] += bool(source_id)
        coverage["with_corr_id"] += bool(corr_id)
        coverage["with_both"] += bool(source_id and corr_id)
        coverage["with_neither"] += not source_id and not corr_id
        if source_id:
            source_geometry[str(source_id)].add(json.dumps(geometry, sort_keys=True))
        for field in ("datetime", "start_datetime", "end_datetime"):
            value = properties.get(field)
            if isinstance(value, str) and value:
                date_values[field].append(value)
        details = properties.get("monty:impact_detail")
        if isinstance(details, dict):
            details = [details]
        if isinstance(details, list):
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                category = str(detail.get("category") or "__missing__")
                unit = "__null__" if detail.get("unit") is None else str(detail.get("unit"))
                impact_type = str(detail.get("type") or "__missing__")
                estimate_type = str(detail.get("estimate_type") or "__missing__")
                categories[category][f"unit:{unit}"] += 1
                categories[category][f"type:{impact_type}"] += 1
                categories[category][f"estimate_type:{estimate_type}"] += 1
        for key, asset in (item.get("assets") or {}).items():
            if not isinstance(asset, dict) or not asset.get("href"):
                continue
            parsed = urlparse(str(asset["href"]))
            assets.append(
                {
                    "key": str(key),
                    "media_type": asset.get("type") or asset.get("media_type"),
                    "href": urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", "")),
                    "query_present": bool(parsed.query),
                    "host": parsed.netloc,
                }
            )

    shared_geometry = Counter()
    for geometries in source_geometry.values():
        if len(geometries) > 1:
            shared_geometry["source_ids_with_multiple_geometries"] += 1
        elif len(geometries) == 1:
            shared_geometry["source_ids_with_one_geometry"] += 1

    coverage_summary = {}
    for collection, values in source_coverage.items():
        coverage_summary[collection] = counter_dict(values)

    date_summary = {}
    for field, values in date_values.items():
        date_summary[field] = {"min": min(values), "max": max(values), "count": len(values)}

    unique_assets = {}
    for asset in assets:
        unique_assets[asset["href"]] = asset

    return (
        {
            "items": len(features),
            "collections": counter_dict(collections),
            "geometry_types": counter_dict(geometry_types),
            "date_fields": date_summary,
            "impact_categories": counter_dict(categories),
            "source_id_coverage": coverage_summary,
            "source_id_geometry_consistency": counter_dict(shared_geometry),
            "top_level_fields": counter_dict(top_level_names),
            "property_fields": counter_dict(property_names),
        },
        list(unique_assets.values())[:20],
    )


def queryable_summary(document: dict) -> dict:
    properties = document.get("properties") or {}
    names = sorted(str(name) for name in properties)
    expected = {
        "datetime",
        "geometry",
        "id",
        "monty:country_codes",
        "monty:corr_id",
        "monty:episode_number",
        "monty:hazard_codes",
        "monty:impact_detail.category",
        "monty:impact_detail.type",
        "roles",
        "processing:version",
    }
    return {
        "advertised_properties": names,
        "expected_missing": sorted(expected.difference(names)),
        "source_id_advertised": "monty:src_event_id" in properties,
    }


def build_body(
    collection: str,
    *,
    start: str,
    end: str,
    country: str | None,
    hazards: list[str],
    impact_type: str | None,
    categories: list[str] | None,
    limit: int,
    projected: bool = True,
) -> dict:
    body = {
        "collections": [collection],
        "limit": limit,
        "filter-lang": "cql2-json",
        "filter": build_filter(
            start=start,
            end=end,
            country=country,
            hazards=hazards,
            impact_type=impact_type,
            categories=categories,
        ),
    }
    if projected:
        body["fields"] = {"include": DEFAULT_FIELDS}
    return body


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.environ.get("MONTANDON_API_URL", DEFAULT_ENDPOINT))
    parser.add_argument("--start", default="2024-01-01T00:00:00Z")
    parser.add_argument("--end", default="2024-02-01T00:00:00Z")
    parser.add_argument("--country", default="PHL")
    parser.add_argument("--hazard", dest="hazards", action="append", default=None)
    parser.add_argument("--impact-type", default="affected_total")
    parser.add_argument("--category", dest="categories", action="append", default=None)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--max-items", type=int, default=500)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--asset-max-bytes", type=int, default=2_000_000)
    parser.add_argument("--output-root", type=Path, default=Path("data"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit < 1 or args.max_pages < 1 or args.max_items < 1:
        raise SystemExit("--limit, --max-pages, and --max-items must be positive")
    endpoint = args.endpoint.rstrip("/")
    parsed_endpoint = urlparse(endpoint)
    if parsed_endpoint.scheme != "https" or not parsed_endpoint.hostname:
        raise SystemExit("--endpoint must be an absolute https:// URL")
    token = token_from_env()
    hazards = args.hazards or ["MH0600", "nat-hyd-flo-flo", "FL"]
    categories = args.categories or ["people", "children_0_4"]
    run_id = utc_stamp()
    raw_dir = args.output_root / "cache" / "api-profile" / run_id
    report_path = args.output_root / "manifests" / f"pdc_api_profile_{run_id}.json"
    api_host = urlparse(endpoint).netloc
    failures = []
    capabilities = {}
    query_records = []

    try:
        root_queryables = request_json(f"{endpoint}/queryables", token, timeout=args.timeout)
        root_queryables_path = raw_dir / "queryables_root.json"
        write_json(root_queryables_path, root_queryables)
        capabilities["root_queryables"] = {
            "queryables_raw": str(root_queryables_path),
            "queryables": queryable_summary(root_queryables),
        }
    except ProbeError as error:
        failures.append({"operation": "queryables", "scope": "root", "error": str(error)})

    for collection in COLLECTIONS:
        collection_record = {}
        try:
            collection_document = request_json(
                f"{endpoint}/collections/{collection}", token, timeout=args.timeout
            )
            collection_path = raw_dir / f"collection_{collection}.json"
            write_json(collection_path, collection_document)
            collection_record["collection_metadata_raw"] = str(collection_path)
        except ProbeError as error:
            failures.append({"operation": "collection", "collection": collection, "error": str(error)})
        try:
            queryables = request_json(
                f"{endpoint}/collections/{collection}/queryables", token, timeout=args.timeout
            )
            queryables_path = raw_dir / f"queryables_{collection}.json"
            write_json(queryables_path, queryables)
            collection_record["queryables_raw"] = str(queryables_path)
            collection_record["queryables"] = queryable_summary(queryables)
        except ProbeError as error:
            failures.append({"operation": "queryables", "collection": collection, "error": str(error)})
        capabilities[collection] = collection_record

    search_specs = [
        (
            "events",
            "pdc-events",
            build_body(
                "pdc-events",
                start=args.start,
                end=args.end,
                country=args.country,
                hazards=hazards,
                impact_type=None,
                categories=None,
                limit=args.limit,
            ),
        ),
        (
            "hazards",
            "pdc-hazards",
            build_body(
                "pdc-hazards",
                start=args.start,
                end=args.end,
                country=args.country,
                hazards=hazards,
                impact_type=None,
                categories=None,
                limit=args.limit,
            ),
        ),
        (
            "impacts",
            "pdc-impacts",
            build_body(
                "pdc-impacts",
                start=args.start,
                end=args.end,
                country=args.country,
                hazards=hazards,
                impact_type=args.impact_type,
                categories=categories,
                limit=args.limit,
            ),
        ),
    ]
    for label, collection, body in search_specs:
        try:
            record = fetch_search(
                endpoint,
                token,
                body,
                label=label,
                raw_dir=raw_dir,
                max_pages=args.max_pages,
                max_items=args.max_items,
                timeout=args.timeout,
            )
            bounded_features = record.pop("items")
            record["label"] = label
            record["collection"] = collection
            record["profile"] = inspect_features(bounded_features)[0]
            query_records.append(record)
        except ProbeError as error:
            failures.append({"operation": "search", "label": label, "collection": collection, "error": str(error)})

    # ID batches are built from the raw page files, avoiding an unbounded response.
    id_batch_records = []
    for collection in COLLECTIONS:
        ids = []
        for path in sorted(raw_dir.glob(f"*_page_*.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            for feature in document.get("features") or []:
                if feature.get("collection") == collection and feature.get("id") and feature["id"] not in ids:
                    ids.append(feature["id"])
                if len(ids) >= 5:
                    break
            if len(ids) >= 5:
                break
        if not ids:
            id_batch_records.append({"collection": collection, "status": "no_sample_ids"})
            continue
        body = {
            "collections": [collection],
            "ids": ids,
            "limit": len(ids),
            "fields": {"include": DEFAULT_FIELDS},
        }
        try:
            record = fetch_search(
                endpoint,
                token,
                body,
                label=f"id_batch_{collection}",
                raw_dir=raw_dir,
                max_pages=3,
                max_items=len(ids) + 1,
                timeout=args.timeout,
            )
            returned_ids = [item.get("id") for item in record.pop("items")]
            record.update(
                {
                    "collection": collection,
                    "requested_ids": len(ids),
                    "returned_ids": len([item for item in returned_ids if item]),
                    "missing_requested_ids": sorted(set(ids).difference(returned_ids)),
                }
            )
            id_batch_records.append(record)
        except ProbeError as error:
            failures.append({"operation": "id_batch", "collection": collection, "error": str(error)})

    asset_checks = []
    asset_seen = set()
    for path in sorted(raw_dir.glob("*_page_*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        for item in document.get("features") or []:
            for key, asset in (item.get("assets") or {}).items():
                href = asset.get("href") if isinstance(asset, dict) else None
                if not href or href in asset_seen or key not in {"Maps", "report"}:
                    continue
                asset_seen.add(href)
                asset_checks.append(
                    {
                        "key": key,
                        "item_id": item.get("id"),
                        "collection": item.get("collection"),
                        "check": request_asset(
                            href,
                            token,
                            api_host=api_host,
                            max_bytes=args.asset_max_bytes,
                            timeout=args.timeout,
                        ),
                    }
                )
                if len(asset_checks) >= 10:
                    break
            if len(asset_checks) >= 10:
                break
        if len(asset_checks) >= 10:
            break

    query_summaries = []
    for record in query_records:
        summary = dict(record)
        query_summaries.append(summary)

    profile = {
        "schema_version": "stage1.pdc_api_profile.v1",
        "status": "complete" if not failures else "partial",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "scope": {
            "collections": list(COLLECTIONS),
            "start": args.start,
            "end": args.end,
            "country": args.country,
            "hazards": hazards,
            "impact_type": args.impact_type,
            "categories": categories,
            "limit": args.limit,
            "max_pages": args.max_pages,
            "max_items": args.max_items,
        },
        "raw_evidence_directory": str(raw_dir),
        "number_matched_policy": "The production API intentionally omits numberMatched; pagination exhaustion is authoritative.",
        "capabilities": capabilities,
        "searches": query_summaries,
        "id_batch_checks": id_batch_records,
        "asset_checks": asset_checks,
        "failures": failures,
        "notes": [
            "This is a bounded profile, not a catalogue download.",
            "Source-ID coverage is measured from returned items; monty:src_event_id is not required as a server queryable.",
            "Asset checks fetch only API-host assets; external hosts are recorded but not fetched.",
            "Raw JSON evidence redacts signed URL query values before writing them to disk.",
        ],
    }
    write_json(report_path, profile)
    print(json.dumps({"status": profile["status"], "report": str(report_path), "raw": str(raw_dir), "failures": failures}, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProbeError as error:
        raise SystemExit(str(error)) from None
