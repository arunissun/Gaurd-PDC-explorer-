"""Offline design-review replay of saved PDC evidence (never used by the interfaces).

The notebook and dashboard retrieve live API data only. This helper exists so
figures can be designed and reviewed against real PDC records without new
production requests. It replays:

* completed API responses saved under ``data/cache/api`` by earlier fresh
  retrievals (filtered by country, collection, and year), and
* optionally the PDC STAC samples named by ``PDC_DESIGN_SAMPLE_DIR`` (a
  directory containing ``pdc-events__*.jsonl.gz`` etc.).

Records go through the real ``PdcEvidenceService`` normalisation path.
"""

from __future__ import annotations

import glob
import gzip
import json
import os
from pathlib import Path

from guard_pdc.api import ApiQueryResult, PartitionResult
from guard_pdc.config import MontandonConfig
from guard_pdc.models import QueryResult, QuerySpec
from guard_pdc.service import PdcEvidenceService
from guard_pdc.taxonomy import CATEGORY_ORDER

COLLECTIONS = ("pdc-events", "pdc-hazards", "pdc-impacts")


def _query(country: str, year: int, end_year: int | None = None) -> QuerySpec:
    return QuerySpec(
        analysis_mode="country_detail",
        country_code=country,
        year=year,
        end_year=end_year,
        months=tuple(range(1, 13)),
        impact_types=("affected_total", "cost"),
        categories=CATEGORY_ORDER,
        source_mode="api_only",
    )


def _result(collection: str, items: dict[str, dict], query: QuerySpec, label: str) -> ApiQueryResult:
    values = tuple(items.values())
    partition = PartitionResult(
        collection=collection, start=f"{query.year}-01-01T00:00:00Z", end=f"{query.last_year + 1}-01-01T00:00:00Z",
        request={"replay": label}, items=values, pages=(), returned_count=len(values), unique_count=len(values),
        duplicate_ids=(), complete=True, stop_reason="pagination_exhausted", cache_key=f"replay-{collection}",
        retrieved_at="replayed", query_fingerprint=query.fingerprint, from_cache=True,
    )
    return ApiQueryResult(collection, values, (partition,), len(values), len(values), (), True, query.fingerprint)


class _ReplayProvider:
    def __init__(self, results: dict[str, ApiQueryResult]):
        self.results = results

    def query_events(self, _query, **_options):
        return self.results["pdc-events"]

    def query_hazards(self, _query, **_options):
        return self.results["pdc-hazards"]

    def query_impacts(self, _query, **_options):
        return self.results["pdc-impacts"]


def _service_result(items: dict[str, dict[str, dict]], query: QuerySpec, label: str) -> QueryResult:
    provider = _ReplayProvider({collection: _result(collection, items[collection], query, label) for collection in COLLECTIONS})
    return PdcEvidenceService(MontandonConfig(api_token="replay"), api_provider=provider).retrieve(query)


def saved_country(country: str, year: int = 2024, cache_root: str = "data/cache/api") -> QueryResult:
    """Replay saved complete 19-category responses for one country and year."""

    items: dict[str, dict[str, dict]] = {collection: {} for collection in COLLECTIONS}
    for manifest_path in glob.glob(f"{cache_root}/*/manifest.json"):
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        if not manifest.get("complete") or not str(manifest.get("start", "")).startswith(str(year)):
            continue
        args = manifest["request"]["filter"]["args"]
        if next((arg["args"][1] for arg in args if arg["op"] == "a_contains"), None) != country:
            continue
        if manifest["collection"] == "pdc-impacts" and json.dumps(args).count("impact_detail.category") < 19:
            continue
        for page in sorted(Path(manifest_path).parent.glob("page_*.json")):
            for item in json.loads(page.read_text(encoding="utf-8")).get("features") or []:
                items[manifest["collection"]][item["id"]] = item
    return _service_result(items, _query(country, year), f"saved-cache-{country}-{year}")


def sample_country(country: str, sample_dir: str | None = None) -> QueryResult:
    """Replay the PDC STAC samples for one country (items whose codes include it)."""

    directory = Path(sample_dir or os.environ["PDC_DESIGN_SAMPLE_DIR"])
    items: dict[str, dict[str, dict]] = {collection: {} for collection in COLLECTIONS}
    years = set()
    for collection in COLLECTIONS:
        for path in directory.glob(f"{collection}__*.jsonl.gz"):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    item = json.loads(line)
                    if country in (item["properties"].get("monty:country_codes") or []):
                        items[collection][item["id"]] = item
                        years.add(int(item["properties"]["datetime"][:4]))
    first, last = min(years), max(years)
    first = max(first, last - 4)
    return _service_result(items, _query(country, first, last if last != first else None), f"sample-{country}")
