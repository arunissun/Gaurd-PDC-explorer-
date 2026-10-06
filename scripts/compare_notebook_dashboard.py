"""Compare the notebook's tables with the dashboard pipeline for the same query (acceptance criterion 14).

First run the notebook with --dump so that its tables are saved:
    uv run --env-file .env python scripts/run_notebook.py --countries PHL,BGD,NPL --years 2024-2024 --dump process-preview-summary
Then compare, live (a second, fresh retrieval through the dashboard's code path) or offline:
    uv run --env-file .env python scripts/compare_notebook_dashboard.py --live
    uv run python scripts/compare_notebook_dashboard.py --pool path/to/pool.json.gz

It checks, per country: event-family IDs, event/hazard/impact counts, link edges, events per month, every
column of the per-event summary (peak and latest values, statuses) and the item tables. Differences are listed,
never hidden. Exit status 1 when anything differs.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import pickle
import sys
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from guard_pdc.analysis import build_analysis_frames, event_summary  # noqa: E402
from guard_pdc.config import MontandonConfig  # noqa: E402
from guard_pdc.models import QuerySpec  # noqa: E402
from guard_pdc.service import PdcEvidenceService  # noqa: E402

problems: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("ok   " if ok else "FAIL ") + label + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        problems.append(label)


def same(left: pd.Series, right: pd.Series) -> tuple[bool, str]:
    """Equal values, with missing equal to missing and tuples compared as lists."""
    left, right = left.reset_index(drop=True), right.reset_index(drop=True)
    if len(left) != len(right):
        return False, f"length {len(left)} vs {len(right)}"
    for index, (a, b) in enumerate(zip(left, right, strict=True)):
        missing_a = a is None or (not isinstance(a, (tuple, list, dict)) and pd.isna(a))
        missing_b = b is None or (not isinstance(b, (tuple, list, dict)) and pd.isna(b))
        if missing_a and missing_b:
            continue
        if isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)) and list(a) == list(b):
            continue
        try:
            if a == b:
                continue
        except ValueError:
            pass
        return False, f"row {index}: {a!r} vs {b!r}"
    return True, ""


def dashboard_results(dump: dict, contexts) -> dict:
    """Retrieve every country of the notebook's query through the dashboard's code path."""
    with ExitStack() as stack, TemporaryDirectory() as folder:
        for context in contexts:
            stack.enter_context(context)
        service = PdcEvidenceService(MontandonConfig(api_token=os.environ["MONTANDON_API_TOKEN"], api_cache_path=Path(folder)))
        results = {}
        for country in dump["COUNTRIES"]:
            q = dump["QUERIES"][country]["query"]
            spec = QuerySpec(analysis_mode=q["analysis_mode"], country_code=q["country_code"], year=q["year"], end_year=q.get("end_year"), months=tuple(q["months"]),
                             hazard_codes=tuple(q["hazard_codes"]), impact_types=tuple(q["impact_types"]), categories=tuple(q["categories"]),
                             source_mode="api_only", refresh_api_cache=True)
            check(f"{country}: query fingerprint equals the dashboard's", spec.fingerprint == dump["QUERIES"][country]["fingerprint"])
            result = service.retrieve(spec)
            frames = build_analysis_frames(result)
            results[country] = (result, frames, event_summary(frames))
        return results


def per_month(frame: pd.DataFrame) -> dict:
    data = frame.dropna(subset=["year", "month"])
    return data.groupby([data["year"].astype(int), data["month"].astype(int)])["family_key"].nunique().to_dict()


def compare_country(country: str, dump: dict, result, frames, summary) -> None:
    tables, mine = dump["TABLES"][country], dump["SUMMARIES"][country]
    print(f"\n=== {country}")
    check(f"{country}: dashboard result complete", result.complete)
    check(f"{country}: event families ({len(mine)})", len(mine) == len(summary), f"{len(mine)} vs {len(summary)}")
    check(f"{country}: event family IDs identical", set(mine["family_key"]) == set(summary["family_key"]))
    check(f"{country}: event snapshots ({len(tables['events'])})", len(tables["events"]) == len(frames.event_snapshots))
    check(f"{country}: hazard items ({len(tables['hazards'])})", len(tables["hazards"]) == len(frames.hazards))
    check(f"{country}: exposure values ({len(tables['impacts'])})", len(tables["impacts"]) == len(frames.impacts))
    check(f"{country}: link edges ({len(tables['edges'])})", len(tables["edges"]) == len(frames.correlations))
    check(f"{country}: events per month identical", per_month(mine) == per_month(summary))
    if set(mine["family_key"]) != set(summary["family_key"]):
        return
    left, right = mine.sort_values("family_key").reset_index(drop=True), summary.sort_values("family_key").reset_index(drop=True)
    for column in right.columns:
        if column == "exposure_class":  # the notebook does not use the dashboard's size-class column
            continue
        ok, detail = same(left[column], right[column]) if column in left.columns else (False, "column missing in the notebook")
        check(f"{country}: summary column {column}", ok, detail)

    def edge_set(frame: pd.DataFrame) -> set:
        return {(r.event_item_id, r.target_collection, r.target_item_id, r.link_method, r.status, int(r.candidate_count), r.review_reason or "") for r in frame.itertuples()}

    check(f"{country}: link edges identical", edge_set(tables["edges"]) == edge_set(frames.correlations))
    for name, theirs, key in (("events", frames.event_snapshots, "event_item_id"), ("hazards", frames.hazards, "hazard_item_id"), ("impacts", frames.impacts, "impact_item_id")):
        order = [key] + (["detail_position"] if name == "impacts" else [])
        ours = tables[name].sort_values(order).reset_index(drop=True)
        other = theirs.sort_values(order).reset_index(drop=True)
        shared = [c for c in other.columns if c in ours.columns and c not in ("provider", "availability", "raw_pointer", "correlation_status", "geometry")]
        bad = [(c, same(ours[c], other[c])[1]) for c in shared if not same(ours[c], other[c])[0]]
        check(f"{country}: {name} table, {len(shared)} columns compared", not bad, str(bad[:3]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="retrieve through the dashboard's code path from the production API")
    parser.add_argument("--pool", default="", help="offline: pool of items served by tests/fake_stac.py")
    parser.add_argument("--dump", default=str(PROJECT / "outputs" / "notebook" / "tables.pkl"))
    args = parser.parse_args()
    with open(args.dump, "rb") as handle:
        dump = pickle.load(handle)
    if args.live:
        results = dashboard_results(dump, [])
    elif args.pool:
        from tests.fake_stac import FakeStac, installed, load_pool

        os.environ.setdefault("MONTANDON_API_TOKEN", "offline-test-token")
        results = dashboard_results(dump, [installed(FakeStac(load_pool(args.pool)))])
    else:
        parser.error("choose --live or --pool")
    for country, (result, frames, summary) in results.items():
        compare_country(country, dump, result, frames, summary)
    print("\nPARITY:", "ALL IDENTICAL" if not problems else f"{len(problems)} DIFFERENCES")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
