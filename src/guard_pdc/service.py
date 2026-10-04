"""Provider routing, evidence merge, normalization, and PDC correlation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import lru_cache
import re
from typing import Any
from urllib.parse import unquote, urlparse

from .api import ApiQueryResult, PdcApiProvider
from .config import MontandonConfig
from .local import LocalQueryResult, PdcLocalProvider
from .models import (
    CorrelationEvidenceRow,
    EventFamilyRow,
    EventSnapshotRow,
    EvidenceProvenance,
    HazardSnapshotRow,
    ImpactObservationRow,
    QueryResult,
    QuerySpec,
    RetrievalMetadata,
    canonical_payload_hash,
)


class ServiceError(RuntimeError):
    """The requested source routing or evidence normalization cannot complete."""


_PDC_UUID = r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}"
_PDC_SOURCE_ID = r"[A-Za-z0-9_.:~-]+"
_VERIFIED_PDC_PROCESSING_VERSIONS = frozenset(
    {"0.1.1", *(f"0.2.{patch}" for patch in range(8))}
)
_EVENT_SNAPSHOT_RE = re.compile(
    rf"^pdc-(?:event|hazard)-{_PDC_UUID}-{_PDC_SOURCE_ID}-(?P<epoch>\d{{10,13}})$"
)
_IMPACT_SNAPSHOT_RE = re.compile(
    rf"^pdc-impact-{_PDC_UUID}-{_PDC_SOURCE_ID}-(?P<epoch>\d{{10,13}})-\d+-(?:population|capital)-[A-Za-z0-9_]+-[A-Za-z]{{3}}$"
)


def derive_pdc_exposure_snapshot_time(
    item_id: str,
    processing_version: object = None,
) -> tuple[str | None, str | None]:
    """Return the PDC exposure timestamp encoded by the transformer in an item ID."""

    if processing_version is not None:
        if not isinstance(processing_version, str):
            return None, None
        normalized_version = processing_version.removeprefix("v")
        if normalized_version not in _VERIFIED_PDC_PROCESSING_VERSIONS:
            return None, None
    pattern = _IMPACT_SNAPSHOT_RE if item_id.startswith("pdc-impact-") else _EVENT_SNAPSHOT_RE
    match = pattern.fullmatch(item_id)
    if match is None:
        return None, None
    seconds = int(match.group("epoch"))
    if seconds >= 1_000_000_000_000:
        seconds //= 1_000
    try:
        value = datetime.fromtimestamp(seconds, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None, None
    if not 2000 <= value.year <= 2100:
        return None, None
    return value.isoformat().replace("+00:00", "Z"), "pdc_item_id_exposure_timestamp"


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    item: Mapping[str, Any]
    provenance: tuple[EvidenceProvenance, ...]


def _properties(record: EvidenceRecord) -> Mapping[str, Any]:
    properties = record.item.get("properties")
    return properties if isinstance(properties, Mapping) else {}


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _details(properties: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    detail = properties.get("monty:impact_detail")
    if isinstance(detail, Mapping):
        return (detail,)
    if isinstance(detail, list):
        return tuple(item for item in detail if isinstance(item, Mapping))
    return ()


@lru_cache(maxsize=262_144)
def _href_segments(href: str) -> tuple[str, ...]:
    return tuple(unquote(segment) for segment in urlparse(href).path.split("/") if segment and segment != "..")


def _related_target(link: Mapping[str, Any]) -> tuple[str | None, str | None]:
    href = link.get("href")
    if not isinstance(href, str) or not href:
        return None, None
    segments = list(_href_segments(href))
    if "collections" in segments:
        position = segments.index("collections")
        if len(segments) > position + 3 and segments[position + 2] == "items":
            return segments[position + 1], segments[position + 3].removesuffix(".json")
    collection = link.get("collection")
    if not isinstance(collection, str):
        collection = next((segment for segment in segments if segment.startswith("pdc-")), None)
    return collection, segments[-1].removesuffix(".json") if segments else None


def _related_links(item: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    links = item.get("links")
    if not isinstance(links, list):
        return ()
    return tuple(link for link in links if isinstance(link, Mapping) and link.get("rel") == "related")


def _unique_provenance(values: Iterable[EvidenceProvenance]) -> tuple[EvidenceProvenance, ...]:
    return tuple(dict.fromkeys(values))


def _set_availability(record: EvidenceRecord, availability: str) -> EvidenceRecord:
    return EvidenceRecord(
        record.item,
        tuple(replace(provenance, availability=availability) for provenance in record.provenance),
    )


def _merge_records(records: Iterable[EvidenceRecord]) -> tuple[EvidenceRecord, ...]:
    by_identity: dict[tuple[str, str], dict[str, list[EvidenceRecord]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        collection = record.item.get("collection")
        item_id = record.item.get("id")
        if not isinstance(collection, str) or not isinstance(item_id, str) or not item_id:
            raise ServiceError("provider returned an item without a collection or ID")
        # Every provenance entry of a record describes the same payload; reuse its
        # hash instead of hashing the item a second time.
        known = next((value.payload_hash for value in record.provenance if value.provider == "api" and value.payload_hash), None)
        by_identity[(collection, item_id)][known or canonical_payload_hash(record.item)].append(record)

    merged = []
    for identity in sorted(by_identity):
        variants = by_identity[identity]
        conflict = len(variants) > 1
        for payload_hash in sorted(variants):
            group = variants[payload_hash]
            provenance = _unique_provenance(value for record in group for value in record.provenance)
            providers = {value.provider for value in provenance}
            availability = "payload_conflict" if conflict else "both" if providers == {"api", "local"} else f"{next(iter(providers))}_only"
            merged.append(_set_availability(EvidenceRecord(group[0].item, provenance), availability))
    return tuple(merged)


def _api_records(result: ApiQueryResult, config: MontandonConfig) -> tuple[EvidenceRecord, ...]:
    records = []
    for partition in result.partitions:
        for item in partition.items:
            item_id = item.get("id")
            collection = item.get("collection")
            if not isinstance(item_id, str) or not isinstance(collection, str):
                raise ServiceError("API returned an item without a collection or ID")
            provenance = EvidenceProvenance(
                provider="api",
                endpoint_or_file=config.endpoint,
                retrieved_at=partition.retrieved_at,
                collection=collection,
                item_id=item_id,
                query_fingerprint=partition.query_fingerprint,
                raw_pointer=f"{config.api_cache_path / partition.cache_key}#{item_id}",
                payload_hash=canonical_payload_hash(item),
            )
            records.append(EvidenceRecord(item, (provenance,)))
    return tuple(records)


def _parse_window(value: object) -> tuple[int, int] | None:
    """(year, month) of an ISO datetime string, or None when unparseable."""

    if not isinstance(value, str) or len(value) < 7 or value[4] != "-":
        return None
    try:
        return int(value[:4]), int(value[5:7])
    except ValueError:
        return None


def _event_windows(items: Iterable[Mapping[str, Any]]) -> set[tuple[int, int]]:
    windows = set()
    for item in items:
        properties = item.get("properties")
        window = _parse_window(properties.get("datetime") if isinstance(properties, Mapping) else None)
        if window is not None:
            windows.add(window)
    return windows


def _local_records(result: LocalQueryResult) -> tuple[EvidenceRecord, ...]:
    return tuple(EvidenceRecord(record.item, (record.provenance,)) for record in result.records)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _time_compatible(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool | None:
    first_start = _parse_time(first.get("start_datetime") or first.get("datetime"))
    first_end = _parse_time(first.get("end_datetime") or first.get("datetime"))
    second_start = _parse_time(second.get("start_datetime") or second.get("datetime"))
    second_end = _parse_time(second.get("end_datetime") or second.get("datetime"))
    if None in (first_start, first_end, second_start, second_end):
        return None
    return max(first_start, second_start) <= min(first_end, second_end)


def _episode_compatible(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool | None:
    left = first.get("monty:episode_number")
    right = second.get("monty:episode_number")
    return None if left is None or right is None else left == right


def _fallback_compatible(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    for name in ("monty:country_codes", "monty:hazard_codes"):
        left = set(_strings(first.get(name)))
        right = set(_strings(second.get(name)))
        if left and right and not left.intersection(right):
            return False
    return _episode_compatible(first, second) is not False and _time_compatible(first, second) is not False


def _edge(
    event: EvidenceRecord,
    target: EvidenceRecord | None,
    *,
    target_collection: str,
    target_item_id: str,
    related_href: str | None,
    method: str,
    candidate_count: int,
) -> CorrelationEvidenceRow:
    event_properties = _properties(event)
    target_properties = _properties(target) if target else {}
    source_id = event_properties.get("monty:src_event_id")
    target_source_id = target_properties.get("monty:src_event_id")
    episode = _episode_compatible(event_properties, target_properties) if target else None
    timing = _time_compatible(event_properties, target_properties) if target else None
    if target is None:
        status, reason = "missing_companion", "related item was not present in the bounded result"
    elif source_id and target_source_id and source_id != target_source_id:
        status, reason = "conflict", "related items have different source event IDs"
    elif method == "legacy_corr_id_fallback" and (
        not event_properties.get("monty:corr_id")
        or event_properties.get("monty:corr_id") != target_properties.get("monty:corr_id")
        or not _fallback_compatible(event_properties, target_properties)
    ):
        status, reason = "conflict", "legacy correlation ID or compatibility checks failed"
    elif candidate_count > 1:
        status, reason = "ambiguous", "multiple candidate records remain"
    elif episode is False or timing is False:
        status, reason = "conflict", "episode or time compatibility failed"
    else:
        status, reason = "validated", None
    provenance = event.provenance + (() if target is None else target.provenance)
    return CorrelationEvidenceRow(
        source_collection="pdc-events",
        source_item_id=str(event.item["id"]),
        target_collection=target_collection,
        target_item_id=target_item_id,
        source_event_id=str(source_id) if source_id is not None else None,
        target_source_event_id=str(target_source_id) if target_source_id is not None else None,
        related_href=related_href,
        related_role="related" if related_href else None,
        episode_compatible=episode,
        time_compatible=timing,
        method=method,
        status=status,
        candidate_count=candidate_count,
        review_reason=reason,
        provenance=_unique_provenance(provenance),
    )


def _dedupe_edges(edges: Iterable[CorrelationEvidenceRow]) -> tuple[CorrelationEvidenceRow, ...]:
    grouped: dict[tuple[str, str, str, str, str], list[CorrelationEvidenceRow]] = defaultdict(list)
    for edge in edges:
        grouped[(edge.source_collection, edge.source_item_id, edge.target_collection, edge.target_item_id, edge.method)].append(edge)
    rank = {"validated": 0, "missing_companion": 1, "ambiguous": 2, "conflict": 3}
    result = []
    for key in sorted(grouped):
        values = grouped[key]
        strongest = max(values, key=lambda value: rank.get(value.status, 4))
        reasons = tuple(dict.fromkeys(value.review_reason for value in values if value.review_reason))
        result.append(
            replace(
                strongest,
                candidate_count=max(value.candidate_count for value in values),
                review_reason="; ".join(reasons) or None,
                provenance=_unique_provenance(
                    provenance for value in values for provenance in value.provenance
                ),
            )
        )
    return tuple(result)


def correlate(records: tuple[EvidenceRecord, ...]) -> tuple[CorrelationEvidenceRow, ...]:
    by_identity: dict[tuple[str, str], list[EvidenceRecord]] = defaultdict(list)
    events = []
    targets = []
    for record in records:
        collection = str(record.item.get("collection"))
        item_id = str(record.item.get("id"))
        by_identity[(collection, item_id)].append(record)
        if collection == "pdc-events":
            events.append(record)
        elif collection in {"pdc-hazards", "pdc-impacts"}:
            targets.append(record)

    edges = []
    related_pairs: set[tuple[str, str, str]] = set()
    for record in records:
        collection = str(record.item.get("collection"))
        for link in _related_links(record.item):
            target_collection, target_item_id = _related_target(link)
            if target_collection not in {"pdc-events", "pdc-hazards", "pdc-impacts"} or not target_item_id:
                continue
            if collection == "pdc-events" and target_collection != "pdc-events":
                event_candidates = (record,)
                target_candidates = tuple(by_identity.get((target_collection, target_item_id), ()))
                edge_target_collection, edge_target_id = target_collection, target_item_id
            elif collection != "pdc-events" and target_collection == "pdc-events":
                event_candidates = tuple(by_identity.get(("pdc-events", target_item_id), ()))
                target_candidates = (record,)
                edge_target_collection, edge_target_id = collection, str(record.item["id"])
            else:
                continue
            for event in event_candidates:
                related_pairs.add((str(event.item["id"]), edge_target_collection, edge_target_id))
                if not target_candidates:
                    if edge_target_collection == "pdc-impacts":
                        continue
                    edges.append(
                        _edge(
                            event,
                            None,
                            target_collection=edge_target_collection,
                            target_item_id=edge_target_id,
                            related_href=str(link.get("href")),
                            method="related_link",
                            candidate_count=0,
                        )
                    )
                for target in target_candidates:
                    event_properties = _properties(event)
                    target_properties = _properties(target)
                    method = "related_link"
                    if not event_properties.get("monty:src_event_id") or not target_properties.get("monty:src_event_id"):
                        method = "legacy_corr_id_fallback"
                    edges.append(
                        _edge(
                            event,
                            target,
                            target_collection=edge_target_collection,
                            target_item_id=edge_target_id,
                            related_href=str(link.get("href")),
                            method=method,
                            candidate_count=len(target_candidates),
                        )
                    )

    events_by_source: dict[str, list[EvidenceRecord]] = defaultdict(list)
    events_by_corr: dict[str, list[EvidenceRecord]] = defaultdict(list)
    for event in events:
        properties = _properties(event)
        source_id = properties.get("monty:src_event_id")
        corr_id = properties.get("monty:corr_id")
        if isinstance(source_id, str) and source_id:
            events_by_source[source_id].append(event)
        if isinstance(corr_id, str) and corr_id:
            events_by_corr[corr_id].append(event)

    # A target whose specific event snapshot is already validated by a related
    # link does not need source-ID candidate edges to every sibling snapshot of
    # the family: those edges were all "ambiguous" by construction and grew as
    # snapshots x impacts (86,660 edges for 4,769 BGD 2024 impacts).
    validated_targets = {
        (edge.target_collection, edge.target_item_id)
        for edge in edges
        if edge.method == "related_link" and edge.status == "validated"
    }
    for target in targets:
        properties = _properties(target)
        target_collection = str(target.item["collection"])
        target_item_id = str(target.item["id"])
        if (target_collection, target_item_id) in validated_targets:
            continue
        source_id = properties.get("monty:src_event_id")
        candidates = events_by_source.get(str(source_id), []) if source_id else []
        method = "source_event_id"
        if not candidates:
            corr_id = properties.get("monty:corr_id")
            if not isinstance(corr_id, str) or not corr_id:
                continue
            candidates = [
                event
                for event in events_by_corr.get(corr_id, [])
                if _fallback_compatible(_properties(event), properties)
            ]
            method = "legacy_corr_id_fallback"
        for event in candidates:
            pair = (str(event.item["id"]), target_collection, target_item_id)
            if pair in related_pairs:
                continue
            edges.append(
                _edge(
                    event,
                    target,
                    target_collection=target_collection,
                    target_item_id=target_item_id,
                    related_href=None,
                    method=method,
                    candidate_count=len(candidates),
                )
            )
    return _dedupe_edges(edges)


def _edge_status(edges: Iterable[CorrelationEvidenceRow]) -> tuple[str | None, str | None]:
    values = tuple(edges)
    if not values:
        return None, "unlinked"
    rank = {"validated": 0, "missing_companion": 1, "ambiguous": 2, "conflict": 3}
    selected = max(values, key=lambda edge: rank.get(edge.status, 4))
    return selected.method, selected.status


def _enrich_provenance(
    provenance: tuple[EvidenceProvenance, ...],
    method: str | None,
    status: str | None,
) -> tuple[EvidenceProvenance, ...]:
    return tuple(replace(value, correlation_method=method, correlation_status=status) for value in provenance)


def _normalize(
    query: QuerySpec,
    records: tuple[EvidenceRecord, ...],
    metadata: tuple[RetrievalMetadata, ...],
) -> QueryResult:
    events = tuple(record for record in records if record.item.get("collection") == "pdc-events")
    hazards = tuple(record for record in records if record.item.get("collection") == "pdc-hazards")
    impacts = tuple(record for record in records if record.item.get("collection") == "pdc-impacts")
    edges = correlate(records)
    edges_by_target: dict[tuple[str, str], list[CorrelationEvidenceRow]] = defaultdict(list)
    for edge in edges:
        edges_by_target[(edge.target_collection, edge.target_item_id)].append(edge)

    snapshots = []
    for record in events:
        properties = _properties(record)
        snapshot_time, snapshot_time_method = derive_pdc_exposure_snapshot_time(
            str(record.item["id"]), properties.get("processing:version")
        )
        linked_hazards = []
        linked_impacts = []
        for link in _related_links(record.item):
            collection, item_id = _related_target(link)
            if collection == "pdc-hazards" and item_id:
                linked_hazards.append(item_id)
            elif collection == "pdc-impacts" and item_id:
                linked_impacts.append(item_id)
        source_id = properties.get("monty:src_event_id")
        method = "source_event_id" if source_id else "legacy_corr_id_fallback" if properties.get("monty:corr_id") else None
        snapshots.append(
            EventSnapshotRow(
                event_item_id=str(record.item["id"]),
                source_event_id=str(source_id) if source_id is not None else None,
                item_datetime=properties.get("datetime"),
                episode_number=properties.get("monty:episode_number"),
                start_datetime=properties.get("start_datetime"),
                end_datetime=properties.get("end_datetime"),
                snapshot_time=snapshot_time,
                snapshot_time_method=snapshot_time_method,
                geometry=record.item.get("geometry"),
                bbox=tuple(record.item["bbox"]) if isinstance(record.item.get("bbox"), list) else None,
                linked_hazard_ids=tuple(dict.fromkeys(linked_hazards)),
                linked_impact_ids=tuple(dict.fromkeys(linked_impacts)),
                assets=record.item.get("assets") if isinstance(record.item.get("assets"), Mapping) else {},
                original_properties=properties,
                provenance=_enrich_provenance(record.provenance, method, "grouped" if method else "unlinked"),
            )
        )

    hazard_snapshots = []
    for record in hazards:
        properties = _properties(record)
        item_id = str(record.item["id"])
        snapshot_time, snapshot_time_method = derive_pdc_exposure_snapshot_time(
            item_id, properties.get("processing:version")
        )
        item_edges = edges_by_target.get(("pdc-hazards", item_id), [])
        method, status = _edge_status(item_edges)
        validated_events = {edge.source_item_id for edge in item_edges if edge.status == "validated"}
        event_item_id = next(iter(validated_events)) if len(validated_events) == 1 else None
        detail = properties.get("monty:hazard_detail")
        detail = detail if isinstance(detail, Mapping) else {}
        hazard_snapshots.append(
            HazardSnapshotRow(
                hazard_item_id=item_id,
                event_item_id=event_item_id,
                source_event_id=properties.get("monty:src_event_id"),
                item_datetime=properties.get("datetime"),
                episode_number=properties.get("monty:episode_number"),
                start_datetime=properties.get("start_datetime"),
                end_datetime=properties.get("end_datetime"),
                snapshot_time=snapshot_time,
                snapshot_time_method=snapshot_time_method,
                country_codes=_strings(properties.get("monty:country_codes")),
                hazard_codes=_strings(properties.get("monty:hazard_codes")),
                geometry=record.item.get("geometry"),
                bbox=tuple(record.item["bbox"]) if isinstance(record.item.get("bbox"), list) else None,
                severity_value=detail.get("severity_value"),
                severity_unit=detail.get("severity_unit"),
                severity_label=detail.get("severity_label"),
                estimate_type=detail.get("estimate_type"),
                original_properties=properties,
                provenance=_enrich_provenance(record.provenance, method, status),
            )
        )

    observations = []
    for record in impacts:
        properties = _properties(record)
        item_id = str(record.item["id"])
        snapshot_time, snapshot_time_method = derive_pdc_exposure_snapshot_time(
            item_id, properties.get("processing:version")
        )
        item_edges = edges_by_target.get(("pdc-impacts", item_id), [])
        method, status = _edge_status(item_edges)
        validated_events = {
            edge.source_item_id for edge in item_edges if edge.status == "validated"
        }
        event_item_id = next(iter(validated_events)) if len(validated_events) == 1 else None
        countries = _strings(properties.get("monty:country_codes"))
        for detail_position, detail in enumerate(_details(properties)):
            value = detail.get("value")
            numeric = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
            observations.append(
                ImpactObservationRow(
                    impact_item_id=item_id,
                    event_item_id=event_item_id,
                    source_event_id=properties.get("monty:src_event_id"),
                    country_code=countries[0] if len(countries) == 1 else None,
                    impact_type=str(detail.get("type") or ""),
                    category=str(detail.get("category") or ""),
                    original_value=value,
                    numeric_value=numeric,
                    original_unit=detail.get("unit"),
                    estimate_type=detail.get("estimate_type"),
                    item_datetime=properties.get("datetime"),
                    detail_position=detail_position,
                    snapshot_time=snapshot_time,
                    snapshot_time_method=snapshot_time_method,
                    geometry=record.item.get("geometry"),
                    processing_version=properties.get("processing:version"),
                    original_properties=properties,
                    provenance=_enrich_provenance(record.provenance, method, status),
                )
            )

    impact_ids_by_source: dict[str, set[str]] = defaultdict(set)
    impact_ids_by_corr: dict[str, set[str]] = defaultdict(set)
    for record in impacts:
        properties = _properties(record)
        if properties.get("monty:src_event_id"):
            impact_ids_by_source[str(properties["monty:src_event_id"])].add(str(record.item["id"]))
        if properties.get("monty:corr_id"):
            impact_ids_by_corr[str(properties["monty:corr_id"])].add(str(record.item["id"]))

    family_groups: dict[str, list[EvidenceRecord]] = defaultdict(list)
    for record in events:
        properties = _properties(record)
        source_id = properties.get("monty:src_event_id")
        corr_id = properties.get("monty:corr_id")
        key = str(source_id) if source_id else f"legacy:{corr_id}" if corr_id else f"item:{record.item['id']}"
        family_groups[key].append(record)
    families = []
    for key in sorted(family_groups):
        group = family_groups[key]
        properties = [_properties(record) for record in group]
        source_ids = {value.get("monty:src_event_id") for value in properties if value.get("monty:src_event_id")}
        source_id = str(next(iter(source_ids))) if len(source_ids) == 1 else None
        corr_ids = {value.get("monty:corr_id") for value in properties if value.get("monty:corr_id")}
        if source_id:
            impact_ids = impact_ids_by_source.get(str(source_id), set())
        elif len(corr_ids) == 1:
            impact_ids = impact_ids_by_corr.get(str(next(iter(corr_ids))), set())
        else:
            impact_ids = set()
        starts = [str(value) for item in properties for value in (item.get("start_datetime") or item.get("datetime"),) if value]
        ends = [str(value) for item in properties for value in (item.get("end_datetime") or item.get("datetime"),) if value]
        provenance = _unique_provenance(value for record in group for value in record.provenance)
        method = "source_event_id" if source_id else "legacy_corr_id_fallback" if corr_ids else None
        families.append(
            EventFamilyRow(
                family_key=key,
                source_event_id=source_id,
                title=next((str(item["title"]) for item in properties if item.get("title")), None),
                start_datetime=min(starts) if starts else None,
                end_datetime=max(ends) if ends else None,
                country_codes=tuple(sorted({code for item in properties for code in _strings(item.get("monty:country_codes"))})),
                hazard_codes=tuple(sorted({code for item in properties for code in _strings(item.get("monty:hazard_codes"))})),
                snapshot_count=len({str(record.item["id"]) for record in group}),
                impact_observation_count=len(impact_ids),
                geometry_types=tuple(sorted({
                    str(record.item["geometry"].get("type"))
                    for record in group
                    if isinstance(record.item.get("geometry"), Mapping) and record.item["geometry"].get("type")
                })),
                providers=tuple(sorted({value.provider for value in provenance})),
                correlation_status="exact_source_id" if source_id else "legacy_corr_id_fallback" if corr_ids else "unlinked",
                provenance=_enrich_provenance(provenance, method, "grouped" if method else "unlinked"),
            )
        )

    warnings = tuple(dict.fromkeys(warning for value in metadata for warning in value.warnings))
    combined = RetrievalMetadata(
        provider="+".join(sorted({value.provider for value in metadata})),
        endpoint_or_file=" | ".join(dict.fromkeys(value.endpoint_or_file for value in metadata)),
        retrieved_at=max((value.retrieved_at for value in metadata), default=datetime.now(timezone.utc).isoformat()),
        query_fingerprint=query.fingerprint,
        local_fingerprint=next((value.local_fingerprint for value in metadata if value.local_fingerprint), None),
        pages=sum(value.pages for value in metadata),
        returned_count=sum(value.returned_count for value in metadata),
        unique_count=len(records),
        complete=all(value.complete for value in metadata),
        warnings=warnings,
        failed_partitions=tuple(value for item in metadata for value in item.failed_partitions),
    )
    quality = {
        "event_families": len(families),
        "event_snapshots": len(snapshots),
        "hazard_records": len(hazards),
        "impact_observations": len(observations),
        "missing_source_ids": sum(not _properties(record).get("monty:src_event_id") for record in records),
        "validated_correlations": sum(edge.status == "validated" for edge in edges),
        "ambiguous_correlations": sum(edge.status == "ambiguous" for edge in edges),
        "correlation_conflicts": sum(edge.status == "conflict" for edge in edges),
        "missing_companions": sum(edge.status == "missing_companion" for edge in edges),
        "payload_conflicts": sum(
            any(value.availability == "payload_conflict" for value in record.provenance)
            for record in records
        ),
        "missing_geometry": sum(record.item.get("geometry") is None for record in records),
    }
    return QueryResult(
        query=query,
        metadata=combined,
        event_families=tuple(families),
        event_snapshots=tuple(snapshots),
        hazard_snapshots=tuple(hazard_snapshots),
        impact_observations=tuple(observations),
        correlation_evidence=edges,
        provider_metadata=metadata,
        quality_summary=quality,
    )


class PdcEvidenceService:
    """One bounded retrieval path shared by the notebook and dashboard."""

    def __init__(
        self,
        config: MontandonConfig,
        *,
        api_provider: PdcApiProvider | None = None,
        local_provider: PdcLocalProvider | None = None,
    ) -> None:
        self.config = config
        self.api_provider = api_provider or (PdcApiProvider(config) if config.api_token else None)
        self.local_provider = local_provider

    def _api_evidence(
        self,
        query: QuerySpec,
        *,
        on_progress: Callable[[Any], None] | None = None,
        previous: Mapping[str, ApiQueryResult] | None = None,
    ) -> tuple[list[EvidenceRecord], list[RetrievalMetadata], dict[str, ApiQueryResult]]:
        if self.api_provider is None:
            raise ServiceError("api source requested but MONTANDON_API_TOKEN is not configured")

        def reuse(collection: str):
            prior = (previous or {}).get(collection)
            return prior.window_results if prior is not None else None

        events = self.api_provider.query_events(query, on_progress=on_progress, reuse=reuse("pdc-events"))
        results = {"pdc-events": events}
        # PDC hazard and impact items carry their event's datetime (verified for
        # 11,460 items on 2026-10-01), so only months that returned events — or
        # whose event window failed and is therefore unknown — can hold them.
        windows = set(_event_windows(events.items))
        windows.update(_parse_window(partition.start) for partition in events.failures)
        windows &= set(query.windows())
        if windows:
            results["pdc-hazards"] = self.api_provider.query_hazards(
                query, windows=windows, on_progress=on_progress, reuse=reuse("pdc-hazards")
            )
            if query.retrieves_impact_detail:
                results["pdc-impacts"] = self.api_provider.query_impacts(
                    query, windows=windows, on_progress=on_progress, reuse=reuse("pdc-impacts")
                )
        return (
            [record for result in results.values() for record in _api_records(result, self.config)],
            [result.metadata(query, self.config.endpoint) for result in results.values()],
            results,
        )

    def _local_evidence(self, query: QuerySpec) -> tuple[list[EvidenceRecord], list[RetrievalMetadata]]:
        if self.local_provider is None and self.config.local_export_path:
            self.local_provider = PdcLocalProvider(
                self.config.local_export_path,
                self.config.local_index_path,
                self.config.manifest_path / "pdc_local_coverage.json",
            )
        if self.local_provider is None:
            raise ServiceError("local source requested but PDC_LOCAL_EXPORT_PATH is not configured")
        events = self.local_provider.query_events(query)
        results = [events]
        if events.items:
            results.append(self.local_provider.query_hazards(query))
        if events.items and query.retrieves_impact_detail:
            results.append(self.local_provider.query_impacts(query))
        return (
            [record for result in results for record in _local_records(result)],
            [result.metadata for result in results],
        )

    def retrieve(
        self,
        query: QuerySpec,
        *,
        on_progress: Callable[[Any], None] | None = None,
        previous: QueryResult | None = None,
    ) -> QueryResult:
        """Retrieve one bounded query.

        ``previous`` is an earlier, incomplete result of the *same* query from
        this session; its completed windows are reused and only failed windows
        are requested again. Any other previous result is ignored.
        """

        if not isinstance(query, QuerySpec):
            raise TypeError("query must be a QuerySpec")
        prior_state = (
            previous.retrieval_state
            if previous is not None and previous.query.fingerprint == query.fingerprint
            else None
        )
        has_local = self.local_provider is not None or self.config.local_export_path is not None
        use_api = query.source_mode in {"api_only", "compare"} or (
            query.source_mode == "best_available" and self.api_provider is not None
        )
        use_local = query.source_mode in {"local_only", "compare"} or (
            query.source_mode == "best_available" and has_local
        )
        if query.source_mode == "compare" and (self.api_provider is None or not has_local):
            raise ServiceError("compare mode requires both API and local providers")
        if not use_api and not use_local:
            raise ServiceError("no configured provider can satisfy this query")

        records: list[EvidenceRecord] = []
        metadata: list[RetrievalMetadata] = []
        state = None
        if use_api:
            api_records, api_metadata, state = self._api_evidence(query, on_progress=on_progress, previous=prior_state)
            records.extend(api_records)
            metadata.extend(api_metadata)
        if use_local:
            local_records, local_metadata = self._local_evidence(query)
            records.extend(local_records)
            metadata.extend(local_metadata)
        return replace(_normalize(query, _merge_records(records), tuple(metadata)), retrieval_state=state)


def retrieve(
    query: QuerySpec,
    config: MontandonConfig | None = None,
    *,
    on_progress: Callable[[Any], None] | None = None,
    previous: QueryResult | None = None,
) -> QueryResult:
    """Retrieve evidence with environment-backed configuration by default."""

    service = PdcEvidenceService(config or MontandonConfig.from_env(require_token=False))
    return service.retrieve(query, on_progress=on_progress, previous=previous)


def retrieve_country_group(
    queries: Iterable[QuerySpec],
    retrieve_fn=None,
    *,
    on_progress: Callable[[str, Any], None] | None = None,
    previous: Mapping[str, QueryResult] | None = None,
) -> dict[str, QueryResult]:
    """Keep country-specific evidence separate, one validated query per country.

    Unrecoverable errors (authentication, invalid configuration) raise and the
    caller keeps its last good result. Recoverable API failures do not raise:
    the affected country result is returned incomplete, with its failed
    windows listed, so that "retry failed parts" can complete it later.
    """

    results: dict[str, QueryResult] = {}
    for query in queries:
        key = query.country_code or "All countries"
        if retrieve_fn is not None:
            results[key] = retrieve_fn(query)
            continue
        progress = (lambda event, key=key: on_progress(key, event)) if on_progress is not None else None
        results[key] = retrieve(query, on_progress=progress, previous=(previous or {}).get(key))
    return results


def country_group_counts(results: Mapping[str, QueryResult]) -> dict[str, int]:
    """Count shared identifiers once, without adding country exposure values."""
    return {
        "countries": len(results),
        "event_families": len({row.family_key for result in results.values() for row in result.event_families}),
        "event_snapshots": len({row.event_item_id for result in results.values() for row in result.event_snapshots}),
    }
