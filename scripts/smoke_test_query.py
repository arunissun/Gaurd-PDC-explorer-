"""Run one bounded, read-only production API provider smoke check."""

from __future__ import annotations

import argparse
import json

from guard_pdc.api import PdcApiProvider
from guard_pdc.config import MontandonConfig
from guard_pdc.models import QuerySpec


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", default="PHL")
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--month", type=int, default=1)
    parser.add_argument("--hazard", action="append", default=[])
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    query = QuerySpec(
        analysis_mode="country_detail",
        country_code=args.country,
        year=args.year,
        months=(args.month,),
        hazard_codes=tuple(args.hazard),
        source_mode="api_only",
        refresh_api_cache=args.refresh,
    )
    provider = PdcApiProvider(MontandonConfig.from_env())
    capabilities = provider.discover(refresh=args.refresh)
    result = provider.query_events(query)
    print(
        json.dumps(
            {
                "status": "complete" if result.complete else "incomplete",
                "collection": result.collection,
                "query_fingerprint": query.fingerprint,
                "pages": sum(len(partition.pages) for partition in result.partitions),
                "returned_count": result.returned_count,
                "unique_count": result.unique_count,
                "duplicate_ids": list(result.duplicate_ids),
                "from_cache": result.from_cache,
                "capabilities_from_cache": capabilities.from_cache,
            },
            indent=2,
        )
    )
    return 0 if result.complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
