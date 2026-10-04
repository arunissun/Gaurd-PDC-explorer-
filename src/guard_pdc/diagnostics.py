"""Read-only diagnostics behind the dashboard's Advanced tab.

Everything here describes a retrieval that already happened: the validated
query, the exact search bodies the provider sends, every retrieval window with
its pages, and the original STAC items behind one event. Nothing requests data,
and every string passes through ``exports.public_text`` so local paths and
credential-like URL parameters never reach the browser. Request headers (and so
the bearer token) are never part of these structures.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

import pandas as pd

from .analysis import AnalysisFrames
from .api import DEFAULT_PAGE_LIMIT, build_search_body, month_bounds
from .exports import public_text
from .models import QueryResult, QuerySpec

COLLECTIONS = ("pdc-events", "pdc-hazards", "pdc-impacts")
# Bounded raw viewer: at most this many items per event, and strings longer
# than RAW_STRING_LIMIT characters are shortened (the item itself is unchanged).
RAW_ITEM_LIMIT = 25
RAW_STRING_LIMIT = 2_000


def public_json(value: Any) -> Any:
    """Copy of a JSON-like value with every string redacted for display."""

    if isinstance(value, str):
        text = public_text(value)
        return text if len(text) <= RAW_STRING_LIMIT else text[:RAW_STRING_LIMIT] + f"… ({len(text):,} characters)"
    if isinstance(value, Mapping):
        return {str(key): public_json(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_json(child) for child in value]
    return value


def query_document(query: QuerySpec) -> dict[str, Any]:
    """The validated query as JSON, with its fingerprint and retrieval windows."""

    windows = query.windows()
    return {
        "query": query.as_dict(),
        "query_fingerprint": query.fingerprint,
        "period": query.period_label,
        "retrieval_windows": len(windows),
        "first_window": "{}-{:02d}".format(*windows[0]) if windows else None,
        "last_window": "{}-{:02d}".format(*windows[-1]) if windows else None,
        "collections": [collection for collection in COLLECTIONS if collection != "pdc-impacts" or query.retrieves_impact_detail],
    }


def example_search_bodies(query: QuerySpec, *, limit: int = DEFAULT_PAGE_LIMIT) -> dict[str, dict[str, Any]]:
    """The POST /search body for the first month of each collection the query uses.

    Every other month uses the same body with its own datetime bounds. When the
    API rejects a large request the provider splits it, so a failed or adapted
    window may have used a smaller variant (see ``partition_log``).
    """

    windows = query.windows()
    if not windows:
        return {}
    start, end = month_bounds(*windows[0])
    bodies = {}
    for collection in query_document(query)["collections"]:
        bodies[collection] = public_json(build_search_body(query, collection, start=start, end=end, limit=limit))
    return bodies


def partition_log(results: Mapping[str, QueryResult]) -> pd.DataFrame:
    """One row per retrieved window (or sub-window) with its pages and outcome."""

    rows = []
    for key, result in results.items():
        for collection, api_result in (result.retrieval_state or {}).items():
            for partition in api_result.partitions:
                entry = partition.log_entry()
                rows.append({
                    "scope": key,
                    "collection": collection,
                    "start": entry["start"],
                    "end": entry["end"],
                    "pages": entry["pages"],
                    "items": entry["items"],
                    "page_limit": entry["limit"],
                    "complete": entry["complete"],
                    "from_cache": partition.from_cache,
                    "stop_reason": partition.stop_reason,
                    "filter_part": entry["filter_part"],
                    "adaptations": ", ".join(entry["adaptations"]),
                    "failure": public_text(entry["failure"]) if entry["failure"] else None,
                    "status": entry["status"],
                })
    columns = ["scope", "collection", "start", "end", "pages", "items", "page_limit", "complete", "from_cache", "stop_reason", "filter_part", "adaptations", "failure", "status"]
    return pd.DataFrame(rows, columns=columns)


def event_item_ids(frames: AnalysisFrames, family_key: str) -> dict[str, list[str]]:
    """Item IDs of every event, hazard and impact snapshot retained for one event family."""

    def ids(frame: pd.DataFrame, column: str) -> list[str]:
        if frame.empty or column not in frame:
            return []
        return list(dict.fromkeys(frame.loc[frame["family_key"] == family_key, column].dropna().astype(str)))

    return {
        "pdc-events": ids(frames.event_snapshots, "event_item_id"),
        "pdc-hazards": ids(frames.hazards, "hazard_item_id"),
        "pdc-impacts": ids(frames.impacts, "impact_item_id"),
    }


def raw_items(result: QueryResult, item_ids: Mapping[str, list[str]], *, limit: int = RAW_ITEM_LIMIT) -> dict[str, list[dict[str, Any]]]:
    """Original STAC items (as returned by the API) for the given IDs, redacted for display.

    Items come from the in-memory retrieval of this session; at most ``limit``
    items are returned per collection, in the order of ``item_ids``.
    """

    state = result.retrieval_state or {}
    found: dict[str, list[dict[str, Any]]] = {}
    for collection, wanted in item_ids.items():
        api_result = state.get(collection)
        if api_result is None or not wanted:
            continue
        index = {str(item.get("id")): item for item in api_result.items if isinstance(item, Mapping)}
        found[collection] = [public_json(index[item_id]) for item_id in wanted[:limit] if item_id in index]
    return found


def json_text(value: Any) -> str:
    """Pretty JSON for a download button."""

    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)
