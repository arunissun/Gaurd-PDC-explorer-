"""Read-only JSONL sidecar index and local PDC provider."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import unquote, urlparse

from .api import COLLECTIONS, month_bounds
from .models import (
    EvidenceProvenance,
    QuerySpec,
    RetrievalMetadata,
    ValidationError,
    canonical_payload_hash,
)


SCHEMA_VERSION = 1
FINGERPRINT_BYTES = 65_536


class LocalProviderError(RuntimeError):
    """The local export or its sidecar index is invalid or incomplete."""


@dataclass(frozen=True, slots=True)
class FileState:
    path: str
    size_bytes: int
    modified_ns: int
    fingerprint: str


@dataclass(frozen=True, slots=True)
class LocalIndexSummary:
    manifest_fingerprint: str
    files: int
    items: int
    reused: bool
    resumed: bool = False


@dataclass(frozen=True, slots=True)
class LocalRecord:
    item: Mapping[str, Any]
    provenance: EvidenceProvenance
    byte_offset: int
    byte_length: int


@dataclass(frozen=True, slots=True)
class LocalQueryResult:
    collection: str
    records: tuple[LocalRecord, ...]
    metadata: RetrievalMetadata
    duplicate_ids: tuple[str, ...] = ()
    from_index: bool = True

    @property
    def items(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(record.item for record in self.records)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _bounded_fingerprint(path: Path) -> str:
    stat = path.stat()
    with path.open("rb") as handle:
        first = handle.read(FINGERPRINT_BYTES)
        if stat.st_size > FINGERPRINT_BYTES:
            handle.seek(max(0, stat.st_size - FINGERPRINT_BYTES))
        last = handle.read(FINGERPRINT_BYTES)
    digest = sha256()
    digest.update(str(stat.st_size).encode("ascii"))
    digest.update(str(stat.st_mtime_ns).encode("ascii"))
    digest.update(first)
    digest.update(last)
    return digest.hexdigest()


def _collect_files(export_path: Path) -> tuple[Path, ...]:
    path = export_path.expanduser().resolve()
    if path.is_file():
        files = (path,) if path.suffix.lower() == ".jsonl" else ()
    elif path.is_dir():
        files = tuple(sorted(item.resolve() for item in path.rglob("*.jsonl") if item.is_file()))
    else:
        raise LocalProviderError(f"local export path does not exist: {path}")
    if not files:
        raise LocalProviderError("local provider currently requires at least one uncompressed .jsonl file")
    return files


def _file_states(export_path: Path) -> tuple[tuple[FileState, ...], str]:
    states = []
    for path in _collect_files(export_path):
        stat = path.stat()
        states.append(FileState(str(path), stat.st_size, stat.st_mtime_ns, _bounded_fingerprint(path)))
    manifest = json.dumps(
        [
            {
                "path": state.path,
                "size_bytes": state.size_bytes,
                "modified_ns": state.modified_ns,
                "fingerprint": state.fingerprint,
            }
            for state in states
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return tuple(states), sha256(manifest.encode("utf-8")).hexdigest()


def _related_target(link: Mapping[str, Any]) -> tuple[str | None, str | None]:
    href = link.get("href")
    if not isinstance(href, str) or not href:
        return None, None
    segments = [unquote(segment) for segment in urlparse(href).path.split("/") if segment and segment != ".."]
    if "collections" in segments:
        position = segments.index("collections")
        if len(segments) > position + 3 and segments[position + 2] == "items":
            return segments[position + 1], segments[position + 3].removesuffix(".json")
    collection = link.get("collection")
    if not isinstance(collection, str) or collection not in COLLECTIONS:
        collection = next((segment for segment in segments if segment in COLLECTIONS), None)
    item_id = segments[-1].removesuffix(".json") if segments else None
    return collection, item_id


def _impact_details(properties: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    details = properties.get("monty:impact_detail")
    if isinstance(details, Mapping):
        return (details,)
    if isinstance(details, list):
        return tuple(detail for detail in details if isinstance(detail, Mapping))
    return ()


def _create_schema(connection: sqlite3.Connection, states: tuple[FileState, ...], manifest: str) -> int:
    connection.executescript(
        f"""
        PRAGMA journal_mode=DELETE;
        PRAGMA foreign_keys=ON;
        PRAGMA user_version={SCHEMA_VERSION};
        CREATE TABLE index_runs (
          run_id INTEGER PRIMARY KEY,
          manifest_fingerprint TEXT NOT NULL,
          started_at TEXT NOT NULL,
          completed_at TEXT,
          status TEXT NOT NULL
        );
        CREATE TABLE files (
          file_id INTEGER PRIMARY KEY,
          path TEXT NOT NULL UNIQUE,
          size_bytes INTEGER NOT NULL,
          modified_ns INTEGER NOT NULL,
          fingerprint TEXT NOT NULL,
          status TEXT NOT NULL,
          item_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE items (
          row_id INTEGER PRIMARY KEY,
          file_id INTEGER NOT NULL REFERENCES files(file_id),
          collection TEXT NOT NULL,
          item_id TEXT NOT NULL,
          source_event_id TEXT,
          corr_id TEXT,
          episode_number INTEGER,
          item_datetime TEXT,
          start_datetime TEXT,
          end_datetime TEXT,
          impact_type TEXT,
          impact_category TEXT,
          geometry_type TEXT,
          byte_offset INTEGER NOT NULL,
          byte_length INTEGER NOT NULL,
          raw_hash TEXT NOT NULL,
          payload_hash TEXT NOT NULL
        );
        CREATE TABLE item_countries (
          item_row_id INTEGER NOT NULL REFERENCES items(row_id) ON DELETE CASCADE,
          country_code TEXT NOT NULL
        );
        CREATE TABLE item_hazards (
          item_row_id INTEGER NOT NULL REFERENCES items(row_id) ON DELETE CASCADE,
          hazard_code TEXT NOT NULL
        );
        CREATE TABLE item_impacts (
          item_row_id INTEGER NOT NULL REFERENCES items(row_id) ON DELETE CASCADE,
          detail_position INTEGER NOT NULL,
          impact_type TEXT,
          category TEXT,
          value_json TEXT,
          unit_json TEXT,
          estimate_type TEXT,
          PRIMARY KEY(item_row_id, detail_position)
        );
        CREATE TABLE related_links (
          item_row_id INTEGER NOT NULL REFERENCES items(row_id) ON DELETE CASCADE,
          target_collection TEXT,
          target_item_id TEXT,
          href TEXT NOT NULL,
          rel TEXT NOT NULL
        );
        CREATE INDEX idx_items_collection_datetime ON items(collection, item_datetime);
        CREATE INDEX idx_items_collection_source ON items(collection, source_event_id);
        CREATE INDEX idx_items_collection_impact_date ON items(collection, impact_type, impact_category, item_datetime);
        CREATE INDEX idx_items_identity ON items(collection, item_id);
        CREATE INDEX idx_countries_code ON item_countries(country_code, item_row_id);
        CREATE INDEX idx_hazards_code ON item_hazards(hazard_code, item_row_id);
        CREATE INDEX idx_impacts_filter ON item_impacts(impact_type, category, item_row_id);
        CREATE INDEX idx_links_target ON related_links(target_collection, target_item_id);
        """
    )
    cursor = connection.execute(
        "INSERT INTO index_runs(manifest_fingerprint, started_at, status) VALUES (?, ?, 'running')",
        (manifest, _utc_now()),
    )
    for state in states:
        connection.execute(
            "INSERT INTO files(path, size_bytes, modified_ns, fingerprint, status) VALUES (?, ?, ?, ?, 'pending')",
            (state.path, state.size_bytes, state.modified_ns, state.fingerprint),
        )
    connection.commit()
    return int(cursor.lastrowid)


def _index_matches(path: Path, manifest: str) -> bool:
    if not path.exists():
        return False
    try:
        with closing(sqlite3.connect(path)) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            row = connection.execute(
                "SELECT manifest_fingerprint, status FROM index_runs ORDER BY run_id DESC LIMIT 1"
            ).fetchone()
        return version == SCHEMA_VERSION and row == (manifest, "complete")
    except sqlite3.DatabaseError:
        return False


def _index_item(connection: sqlite3.Connection, file_id: int, item: Mapping[str, Any], raw: bytes, offset: int) -> None:
    collection = item.get("collection")
    item_id = item.get("id")
    properties = item.get("properties")
    if collection not in COLLECTIONS or not isinstance(item_id, str) or not item_id:
        raise LocalProviderError("local JSONL contains an unsupported collection or missing item ID")
    if not isinstance(properties, Mapping):
        raise LocalProviderError(f"{collection}/{item_id} has non-object properties")
    details = _impact_details(properties)
    first_detail = details[0] if details else {}
    geometry = item.get("geometry")
    geometry_type = geometry.get("type") if isinstance(geometry, Mapping) else None
    cursor = connection.execute(
        """
        INSERT INTO items(
          file_id, collection, item_id, source_event_id, corr_id, episode_number,
          item_datetime, start_datetime, end_datetime, impact_type,
          impact_category, geometry_type, byte_offset, byte_length, raw_hash,
          payload_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            file_id,
            collection,
            item_id,
            properties.get("monty:src_event_id"),
            properties.get("monty:corr_id"),
            properties.get("monty:episode_number"),
            properties.get("datetime"),
            properties.get("start_datetime"),
            properties.get("end_datetime"),
            first_detail.get("type"),
            first_detail.get("category"),
            geometry_type,
            offset,
            len(raw),
            sha256(raw).hexdigest(),
            canonical_payload_hash(item),
        ),
    )
    row_id = int(cursor.lastrowid)
    countries = properties.get("monty:country_codes")
    if isinstance(countries, list):
        connection.executemany(
            "INSERT INTO item_countries(item_row_id, country_code) VALUES (?, ?)",
            ((row_id, str(country)) for country in dict.fromkeys(countries)),
        )
    hazards = properties.get("monty:hazard_codes")
    if isinstance(hazards, list):
        connection.executemany(
            "INSERT INTO item_hazards(item_row_id, hazard_code) VALUES (?, ?)",
            ((row_id, str(hazard)) for hazard in dict.fromkeys(hazards)),
        )
    for position, detail in enumerate(details):
        connection.execute(
            """
            INSERT INTO item_impacts(
              item_row_id, detail_position, impact_type, category, value_json,
              unit_json, estimate_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                position,
                detail.get("type"),
                detail.get("category"),
                json.dumps(detail.get("value"), ensure_ascii=False, separators=(",", ":")),
                json.dumps(detail.get("unit"), ensure_ascii=False, separators=(",", ":")),
                detail.get("estimate_type"),
            ),
        )
    links = item.get("links")
    if isinstance(links, list):
        for link in links:
            if not isinstance(link, Mapping) or link.get("rel") != "related" or not isinstance(link.get("href"), str):
                continue
            target_collection, target_item_id = _related_target(link)
            connection.execute(
                """
                INSERT INTO related_links(item_row_id, target_collection, target_item_id, href, rel)
                VALUES (?, ?, ?, ?, 'related')
                """,
                (row_id, target_collection, target_item_id, link["href"]),
            )


def _index_file(connection: sqlite3.Connection, file_id: int, path: Path) -> int:
    count = 0
    with connection, path.open("rb") as handle:
        line_number = 0
        while True:
            offset = handle.tell()
            raw = handle.readline()
            if not raw:
                break
            line_number += 1
            if not raw.strip():
                continue
            try:
                item = json.loads(raw.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise LocalProviderError(f"invalid JSON in {path} at line {line_number}: {type(error).__name__}") from None
            if not isinstance(item, Mapping):
                raise LocalProviderError(f"non-object JSON in {path} at line {line_number}")
            _index_item(connection, file_id, item, raw, offset)
            count += 1
        connection.execute("UPDATE files SET status = 'complete', item_count = ? WHERE file_id = ?", (count, file_id))
    return count


def _coverage(connection: sqlite3.Connection, states: tuple[FileState, ...], manifest: str) -> dict[str, Any]:
    def counts(sql: str) -> dict[str, int]:
        return {str(key): int(value) for key, value in connection.execute(sql)}

    duplicate_ids, conflicting_ids = connection.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(payloads > 1), 0)
        FROM (
          SELECT collection, item_id, COUNT(DISTINCT payload_hash) AS payloads
          FROM items GROUP BY collection, item_id HAVING COUNT(*) > 1
        )
        """
    ).fetchone()
    date_min, date_max = connection.execute("SELECT MIN(item_datetime), MAX(item_datetime) FROM items").fetchone()
    total, with_source = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(source_event_id IS NOT NULL AND source_event_id != ''), 0) FROM items"
    ).fetchone()
    source_id_coverage = {
        str(collection): {"items": int(items), "with_source_event_id": int(present)}
        for collection, items, present in connection.execute(
            """
            SELECT collection, COUNT(*),
                   COALESCE(SUM(source_event_id IS NOT NULL AND source_event_id != ''), 0)
            FROM items GROUP BY collection ORDER BY collection
            """
        )
    }
    warnings = []
    if duplicate_ids:
        warnings.append(f"{int(duplicate_ids)} collection-scoped item IDs are repeated")
    if conflicting_ids:
        warnings.append(f"{int(conflicting_ids)} repeated item IDs have conflicting payloads")
    return {
        "schema_version": "stage4.pdc_local_coverage.v1",
        "created_at": _utc_now(),
        "manifest_fingerprint": manifest,
        "files": [
            {
                "path": state.path,
                "size_bytes": state.size_bytes,
                "modified_ns": state.modified_ns,
                "fingerprint": state.fingerprint,
            }
            for state in states
        ],
        "coverage": {
            "collections": counts("SELECT collection, COUNT(*) FROM items GROUP BY collection ORDER BY collection"),
            "year_month": counts("SELECT SUBSTR(item_datetime, 1, 7), COUNT(*) FROM items WHERE item_datetime IS NOT NULL GROUP BY 1 ORDER BY 1"),
            "countries": counts("SELECT country_code, COUNT(*) FROM item_countries GROUP BY country_code ORDER BY country_code"),
            "hazards": counts("SELECT hazard_code, COUNT(*) FROM item_hazards GROUP BY hazard_code ORDER BY hazard_code"),
            "categories": counts("SELECT category, COUNT(*) FROM item_impacts WHERE category IS NOT NULL GROUP BY category ORDER BY category"),
            "geometry_types": counts("SELECT COALESCE(geometry_type, 'missing'), COUNT(*) FROM items GROUP BY 1 ORDER BY 1"),
            "date_min": date_min,
            "date_max": date_max,
            "items": int(total),
            "with_source_event_id": int(with_source),
            "source_id_coverage": source_id_coverage,
            "duplicate_item_ids": int(duplicate_ids),
            "payload_conflicts": int(conflicting_ids),
        },
        "warnings": warnings,
    }


class PdcLocalProvider:
    """Indexed read-only access to a user-supplied PDC STAC JSONL export."""

    def __init__(self, export_path: Path, index_path: Path, manifest_path: Path):
        self.export_path = Path(export_path)
        self.index_path = Path(index_path)
        self.manifest_path = Path(manifest_path)

    def build_index(self) -> LocalIndexSummary:
        states, manifest = _file_states(self.export_path)
        if _index_matches(self.index_path, manifest):
            with closing(sqlite3.connect(self.index_path)) as connection:
                items = int(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0])
            return LocalIndexSummary(manifest, len(states), items, reused=True)

        # ponytail: immutable exports rebuild as one sidecar; add per-file refresh only if mutable exports make rebuilds costly.
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        building = self.index_path.with_suffix(self.index_path.suffix + ".building")
        resumed = False
        connection: sqlite3.Connection | None = None
        if building.exists():
            try:
                connection = sqlite3.connect(building)
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                run = connection.execute(
                    "SELECT run_id, manifest_fingerprint, status FROM index_runs ORDER BY run_id DESC LIMIT 1"
                ).fetchone()
                if version == SCHEMA_VERSION and run and run[1:] == (manifest, "running"):
                    run_id = int(run[0])
                    resumed = True
                else:
                    connection.close()
                    connection = None
                    building.unlink()
            except sqlite3.DatabaseError:
                if connection is not None:
                    connection.close()
                connection = None
                building.unlink()

        if connection is None:
            connection = sqlite3.connect(building)
            run_id = _create_schema(connection, states, manifest)

        try:
            pending = connection.execute("SELECT file_id, path FROM files WHERE status != 'complete' ORDER BY file_id").fetchall()
            for file_id, path in pending:
                _index_file(connection, int(file_id), Path(path))
            total_items = int(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0])
            coverage = _coverage(connection, states, manifest)
            with connection:
                connection.execute(
                    "UPDATE index_runs SET status = 'complete', completed_at = ? WHERE run_id = ?",
                    (_utc_now(), run_id),
                )
        except Exception:
            connection.close()
            raise
        connection.close()
        building.replace(self.index_path)
        _write_json(self.manifest_path, coverage)
        return LocalIndexSummary(manifest, len(states), total_items, reused=False, resumed=resumed)

    def _select_rows(
        self,
        query: QuerySpec,
        collection: str,
        *,
        ids: tuple[str, ...] = (),
    ) -> tuple[LocalIndexSummary, list[sqlite3.Row]]:
        if collection not in COLLECTIONS:
            raise ValueError(f"unsupported PDC collection: {collection}")
        if collection == "pdc-impacts" and not query.retrieves_impact_detail:
            raise ValidationError(("impact-detail retrieval requires country_detail",))
        summary = self.build_index()
        clauses = ["i.collection = ?"]
        parameters: list[object] = [collection]
        if ids:
            clauses.append(f"i.item_id IN ({','.join('?' for _ in ids)})")
            parameters.extend(ids)
        else:
            month_clauses = []
            for year, month in query.windows():
                start, end = month_bounds(year, month)
                month_clauses.append("(i.item_datetime >= ? AND i.item_datetime < ?)")
                parameters.extend((start, end))
            clauses.append(f"({' OR '.join(month_clauses)})")
            if query.country_code:
                clauses.append("EXISTS (SELECT 1 FROM item_countries c WHERE c.item_row_id = i.row_id AND c.country_code = ?)")
                parameters.append(query.country_code)
            if query.hazard_codes:
                clauses.append(
                    f"EXISTS (SELECT 1 FROM item_hazards h WHERE h.item_row_id = i.row_id AND h.hazard_code IN ({','.join('?' for _ in query.hazard_codes)}))"
                )
                parameters.extend(query.hazard_codes)
            if collection == "pdc-impacts":
                impact_clauses = [
                    f"d.impact_type IN ({','.join('?' for _ in query.impact_types)})",
                    f"d.category IN ({','.join('?' for _ in query.categories)})",
                ]
                clauses.append(
                    "EXISTS (SELECT 1 FROM item_impacts d WHERE d.item_row_id = i.row_id AND "
                    + " AND ".join(impact_clauses)
                    + ")"
                )
                parameters.extend(query.impact_types)
                parameters.extend(query.categories)
        sql = f"""
            SELECT i.*, f.path AS raw_file
            FROM items i JOIN files f ON f.file_id = i.file_id
            WHERE {' AND '.join(clauses)}
            ORDER BY i.item_datetime, i.item_id, i.row_id
        """
        with closing(sqlite3.connect(self.index_path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(sql, parameters).fetchall()
        return summary, rows

    def _load_records(
        self,
        rows: Iterable[sqlite3.Row],
        *,
        query_fingerprint: str,
        local_fingerprint: str,
    ) -> tuple[LocalRecord, ...]:
        handles: dict[str, Any] = {}
        records = []
        retrieved_at = _utc_now()
        try:
            for row in rows:
                path = str(row["raw_file"])
                if path not in handles:
                    handles[path] = Path(path).open("rb")
                handle = handles[path]
                handle.seek(int(row["byte_offset"]))
                raw = handle.read(int(row["byte_length"]))
                if sha256(raw).hexdigest() != row["raw_hash"]:
                    raise LocalProviderError(f"local source changed after indexing: {path}")
                try:
                    item = json.loads(raw.decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise LocalProviderError(f"indexed raw record is no longer valid JSON: {path}") from None
                if canonical_payload_hash(item) != row["payload_hash"]:
                    raise LocalProviderError(f"local payload changed after indexing: {path}")
                provenance = EvidenceProvenance(
                    provider="local",
                    endpoint_or_file=path,
                    retrieved_at=retrieved_at,
                    collection=str(row["collection"]),
                    item_id=str(row["item_id"]),
                    query_fingerprint=query_fingerprint,
                    local_fingerprint=local_fingerprint,
                    raw_pointer=f"{path}:{row['byte_offset']}:{row['byte_length']}",
                    payload_hash=str(row["payload_hash"]),
                )
                records.append(LocalRecord(item, provenance, int(row["byte_offset"]), int(row["byte_length"])))
        finally:
            for handle in handles.values():
                handle.close()
        return tuple(records)

    def query_collection(self, query: QuerySpec, collection: str) -> LocalQueryResult:
        if not isinstance(query, QuerySpec):
            raise TypeError("query must be a QuerySpec")
        summary, rows = self._select_rows(query, collection)
        records = self._load_records(rows, query_fingerprint=query.fingerprint, local_fingerprint=summary.manifest_fingerprint)
        ids = [str(record.item.get("id")) for record in records]
        duplicate_ids = tuple(sorted(item_id for item_id, count in Counter(ids).items() if count > 1))
        warnings = list(query.warnings)
        if duplicate_ids:
            warnings.append(f"local index returned {len(duplicate_ids)} repeated item IDs")
        metadata = RetrievalMetadata(
            provider="local",
            endpoint_or_file=str(self.export_path.resolve()),
            retrieved_at=_utc_now(),
            query_fingerprint=query.fingerprint,
            local_fingerprint=summary.manifest_fingerprint,
            returned_count=len(records),
            unique_count=len(set(ids)),
            complete=True,
            warnings=tuple(warnings),
        )
        return LocalQueryResult(collection, records, metadata, duplicate_ids)

    def query_events(self, query: QuerySpec) -> LocalQueryResult:
        return self.query_collection(query, "pdc-events")

    def query_hazards(self, query: QuerySpec) -> LocalQueryResult:
        return self.query_collection(query, "pdc-hazards")

    def query_impacts(self, query: QuerySpec) -> LocalQueryResult:
        return self.query_collection(query, "pdc-impacts")

    def get_records_by_ids(self, query: QuerySpec, collection: str, ids: Iterable[str]) -> tuple[LocalRecord, ...]:
        requested = tuple(dict.fromkeys(str(item_id) for item_id in ids if str(item_id)))
        if not requested:
            return ()
        all_rows: list[sqlite3.Row] = []
        summary: LocalIndexSummary | None = None
        for offset in range(0, len(requested), 900):
            summary, rows = self._select_rows(query, collection, ids=requested[offset : offset + 900])
            all_rows.extend(rows)
        assert summary is not None
        return self._load_records(all_rows, query_fingerprint=query.fingerprint, local_fingerprint=summary.manifest_fingerprint)
