"""Profile a local PDC export without changing it or loading JSONL into memory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


MAX_FINGERPRINT_BYTES = 65_536
SUPPORTED_JSONL = {".jsonl"}


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def bounded_fingerprint(path: Path) -> str:
    stat = path.stat()
    with path.open("rb") as handle:
        first = handle.read(MAX_FINGERPRINT_BYTES)
        if stat.st_size > MAX_FINGERPRINT_BYTES:
            handle.seek(max(0, stat.st_size - MAX_FINGERPRINT_BYTES))
        last = handle.read(MAX_FINGERPRINT_BYTES)
    digest = hashlib.sha256()
    digest.update(str(stat.st_size).encode())
    digest.update(str(stat.st_mtime_ns).encode())
    digest.update(first)
    digest.update(last)
    return digest.hexdigest()


def counter_dict(value: Counter | dict) -> dict:
    if isinstance(value, Counter):
        return {str(key): count for key, count in sorted(value.items(), key=lambda item: str(item[0]))}
    return {str(key): counter_dict(item) if isinstance(item, (Counter, dict)) else item for key, item in value.items()}


def collect_inputs(path: Path) -> tuple[list[Path], list[Path]]:
    if path.is_file():
        return ([path] if path.suffix.lower() in SUPPORTED_JSONL or path.suffix.lower() == ".json" else []), []
    if not path.is_dir():
        raise FileNotFoundError(f"Local export path does not exist: {path}")
    candidates = sorted(item for item in path.rglob("*") if item.is_file())
    supported = [item for item in candidates if item.suffix.lower() in SUPPORTED_JSONL or item.suffix.lower() == ".json"]
    unsupported = [item for item in candidates if item.suffix.lower() not in SUPPORTED_JSONL | {".json"}]
    return supported, unsupported


def state_template() -> dict:
    return {
        "items": 0,
        "blank_lines": 0,
        "invalid_json": 0,
        "non_object_items": 0,
        "missing_id": 0,
        "missing_collection": 0,
        "collections": Counter(),
        "geometry_types": Counter(),
        "date_fields": {},
        "countries": Counter(),
        "hazards": Counter(),
        "categories": defaultdict(Counter),
        "property_fields": Counter(),
        "top_level_fields": Counter(),
        "source_id_coverage": defaultdict(Counter),
        "file_records": [],
    }


def update_item(state: dict, item: dict, seen: sqlite3.Connection) -> None:
    state["items"] += 1
    state["top_level_fields"].update(item.keys())
    collection = str(item.get("collection") or "__missing__")
    if collection == "__missing__":
        state["missing_collection"] += 1
    state["collections"][collection] += 1

    item_id = item.get("id")
    if not item_id:
        state["missing_id"] += 1
    else:
        canonical = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        payload_hash = hashlib.sha256(canonical).hexdigest()
        existing = seen.execute(
            "SELECT count, first_hash FROM seen WHERE collection = ? AND item_id = ?",
            (collection, str(item_id)),
        ).fetchone()
        if existing:
            seen.execute(
                "UPDATE seen SET count = count + 1, conflict_count = conflict_count + ? WHERE collection = ? AND item_id = ?",
                (int(existing[1] != payload_hash), collection, str(item_id)),
            )
        else:
            seen.execute(
                "INSERT INTO seen(collection, item_id, count, first_hash, conflict_count) VALUES (?, ?, 1, ?, 0)",
                (collection, str(item_id), payload_hash),
            )
    geometry_type = (item.get("geometry") or {}).get("type") or "missing"
    state["geometry_types"][str(geometry_type)] += 1
    properties = item.get("properties") or {}
    if not isinstance(properties, dict):
        properties = {}
    state["property_fields"].update(properties.keys())
    source_id = properties.get("monty:src_event_id")
    corr_id = properties.get("monty:corr_id")
    coverage = state["source_id_coverage"][collection]
    coverage["items"] += 1
    coverage["with_src_event_id"] += bool(source_id)
    coverage["with_corr_id"] += bool(corr_id)
    coverage["with_both"] += bool(source_id and corr_id)
    coverage["with_neither"] += not source_id and not corr_id
    for field in ("datetime", "start_datetime", "end_datetime"):
        value = properties.get(field)
        if isinstance(value, str) and value:
            summary = state["date_fields"].setdefault(field, {"min": value, "max": value, "count": 0})
            summary["min"] = min(summary["min"], value)
            summary["max"] = max(summary["max"], value)
            summary["count"] += 1
    for country in properties.get("monty:country_codes") or []:
        state["countries"][str(country)] += 1
    for hazard in properties.get("monty:hazard_codes") or []:
        state["hazards"][str(hazard)] += 1
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
            state["categories"][category][f"unit:{unit}"] += 1
            state["categories"][category][f"type:{impact_type}"] += 1
            state["categories"][category][f"estimate_type:{estimate_type}"] += 1


def scan_jsonl(path: Path, state: dict, seen: sqlite3.Connection) -> None:
    with path.open("rb") as handle:
        for raw in handle:
            if not raw.strip():
                state["blank_lines"] += 1
                continue
            try:
                item = json.loads(raw.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                state["invalid_json"] += 1
                continue
            if not isinstance(item, dict):
                state["non_object_items"] += 1
                continue
            update_item(state, item, seen)


def scan_json(path: Path, state: dict, seen: sqlite3.Connection, max_bytes: int) -> str | None:
    if path.stat().st_size > max_bytes:
        return f"skipped_over_max_json_bytes:{max_bytes}"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return f"invalid_json:{type(error).__name__}"
    items = document.get("features") if isinstance(document, dict) and document.get("type") == "FeatureCollection" else None
    if not isinstance(items, list):
        return "unsupported_json_shape_expected_feature_collection"
    for item in items:
        if isinstance(item, dict):
            update_item(state, item, seen)
        else:
            state["non_object_items"] += 1
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=None, help="JSONL file or directory; defaults to PDC_LOCAL_EXPORT_PATH")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--work-dir", type=Path, default=Path("data/cache"))
    parser.add_argument("--max-json-bytes", type=int, default=100_000_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configured = args.path or (Path(os.environ["PDC_LOCAL_EXPORT_PATH"]) if os.environ.get("PDC_LOCAL_EXPORT_PATH") else None)
    if configured is None:
        print("No local export path configured. Set PDC_LOCAL_EXPORT_PATH or pass --path.")
        return 2
    inputs, unsupported = collect_inputs(configured)
    if not inputs:
        print("No supported .jsonl or small .json input found at the configured local export path.")
        return 2

    run_id = utc_stamp()
    report_path = args.output or Path("data/manifests") / f"local_pdc_profile_{run_id}.json"
    seen_path = args.work_dir / f"local_profile_{run_id}.sqlite"
    args.work_dir.mkdir(parents=True, exist_ok=True)
    state = state_template()
    scan_errors = []
    seen = sqlite3.connect(seen_path)
    seen.executescript(
        """
        PRAGMA journal_mode=DELETE;
        CREATE TABLE seen (
          collection TEXT NOT NULL,
          item_id TEXT NOT NULL,
          count INTEGER NOT NULL,
          first_hash TEXT NOT NULL,
          conflict_count INTEGER NOT NULL,
          PRIMARY KEY(collection, item_id)
        );
        """
    )
    try:
        for path in inputs:
            stat = path.stat()
            file_record = {
                "path": str(path),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "fingerprint": bounded_fingerprint(path),
                "format": path.suffix.lower().lstrip("."),
            }
            try:
                if path.suffix.lower() == ".jsonl":
                    scan_jsonl(path, state, seen)
                else:
                    error = scan_json(path, state, seen, args.max_json_bytes)
                    if error:
                        file_record["status"] = error
                        scan_errors.append({"path": str(path), "error": error})
            except OSError as error:
                file_record["status"] = f"read_error:{type(error).__name__}"
                scan_errors.append({"path": str(path), "error": file_record["status"]})
            state["file_records"].append(file_record)
            seen.commit()
    finally:
        duplicate_ids, duplicate_rows, payload_conflicts = seen.execute(
            "SELECT COUNT(*), COALESCE(SUM(count - 1), 0), COALESCE(SUM(conflict_count), 0) FROM seen WHERE count > 1"
        ).fetchone()
        seen.close()
        seen_path.unlink(missing_ok=True)

    result = {
        "schema_version": "stage1.local_pdc_profile.v1",
        "status": "complete" if not scan_errors and not state["invalid_json"] else "partial",
        "profiled_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "configured_path": str(configured),
            "supported_files": len(inputs),
            "unsupported_files": len(unsupported),
            "unsupported_extensions": counter_dict(Counter(path.suffix.lower() for path in unsupported)),
        },
        "files": state["file_records"],
        "records": {
            "items": state["items"],
            "blank_lines": state["blank_lines"],
            "invalid_json": state["invalid_json"],
            "non_object_items": state["non_object_items"],
            "missing_id": state["missing_id"],
            "missing_collection": state["missing_collection"],
            "duplicate_item_ids": duplicate_ids,
            "duplicate_rows": duplicate_rows,
            "payload_conflicts_for_duplicate_ids": payload_conflicts,
        },
        "coverage": {
            "collections": counter_dict(state["collections"]),
            "datetime_fields": state["date_fields"],
            "geometry_types": counter_dict(state["geometry_types"]),
            "countries": counter_dict(state["countries"]),
            "hazards": counter_dict(state["hazards"]),
            "source_id_coverage": counter_dict(state["source_id_coverage"]),
            "categories": counter_dict(state["categories"]),
        },
        "schema": {
            "top_level_fields": counter_dict(state["top_level_fields"]),
            "property_fields": counter_dict(state["property_fields"]),
        },
        "scan_errors": scan_errors,
        "notes": [
            "The input files were opened read-only; only an ignored temporary SQLite duplicate index and report were written.",
            "JSONL is streamed line by line. Large JSON files are skipped above --max-json-bytes.",
            "Snapshot time is not inferred from item IDs in this profile.",
        ],
    }
    write_json(report_path, result)
    print(json.dumps({"status": result["status"], "report": str(report_path), "records": result["records"]}, indent=2))
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
