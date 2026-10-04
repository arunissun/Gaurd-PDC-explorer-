"""Build or reuse the read-only local PDC SQLite sidecar index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from guard_pdc.config import MontandonConfig
from guard_pdc.local import PdcLocalProvider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=None, help="Local STAC JSONL file or directory")
    parser.add_argument("--index", type=Path, default=None, help="Derived SQLite sidecar path")
    parser.add_argument("--manifest", type=Path, default=None, help="Derived coverage manifest path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = MontandonConfig.from_env(require_token=False)
    export_path = args.path or config.local_export_path
    if export_path is None:
        raise SystemExit("Set PDC_LOCAL_EXPORT_PATH or pass --path")
    provider = PdcLocalProvider(
        export_path,
        args.index or config.local_index_path,
        args.manifest or config.manifest_path / "pdc_local_coverage.json",
    )
    summary = provider.build_index()
    print(
        json.dumps(
            {
                "status": "reused" if summary.reused else "complete",
                "resumed": summary.resumed,
                "files": summary.files,
                "items": summary.items,
                "manifest_fingerprint": summary.manifest_fingerprint,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
