"""Stage 6 analytical tables built from normalized PDC evidence."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import numpy as np
import pandas as pd

from .models import EvidenceProvenance, QueryResult, QuerySpec, RetrievalMetadata
from .taxonomy import (
    AGE_BAND_CATEGORIES,
    CATEGORY_ORDER,
    MEASURE_CATEGORY,
    TYPE_CATEGORIES,
    category_group,
    category_label,
    code_title_caveat,
    hazard_group,
    hazard_groups,
    ordered_categories,
    pdc_hazard_type,
)


SNAPSHOT_RULE = "latest available item datetime, then item ID and detail position"
PEAK_RULE = "peak (maximum) value across the event's retained PDC exposure snapshots; the latest snapshot value is shown alongside"
ALERT_LEVELS = ("INFORMATION", "ADVISORY", "WATCH", "WARNING")
EXPOSURE_CLASSES = ("Zero", "Under 10k", "10k–100k", "100k–1M", "1M or more")
_SENSITIVE_QUERY = ("token", "secret", "signature", "credential", "accesskey", "authorization", "expires")
_CURRENCY_QUOTE = re.compile(
    r"\$\s?[\d.,]+\s*(?:Thousand|Million|Billion|Trillion)?\s*\(USD\)\s*of infrastructure",
    re.IGNORECASE,
)
TEMPORAL_NOTE = (
    "Time is the PDC exposure timestamp derived from the item ID; not observed impact-report or ingestion time."
)


@dataclass(frozen=True, slots=True)
class AnalysisFrames:
    """Related tables kept at their declared analytical grains."""

    query: QuerySpec
    metadata: RetrievalMetadata
    event_families: pd.DataFrame
    event_snapshots: pd.DataFrame
    hazards: pd.DataFrame
    impacts: pd.DataFrame
    category_status: pd.DataFrame
    demographics: pd.DataFrame
    geometry: pd.DataFrame
    correlations: pd.DataFrame
    provenance: pd.DataFrame
    quality: pd.DataFrame
    snapshot_rule: str = SNAPSHOT_RULE


@dataclass(frozen=True, slots=True)
class TemporalSummary:
    status: str
    valid_snapshots: int
    change_points: int
    message: str


def _joined(values: Any) -> str:
    return ", ".join(str(value) for value in values if value is not None)


def _providers(values: tuple[EvidenceProvenance, ...]) -> str:
    return _joined(sorted({value.provider for value in values}))


def _availability(values: tuple[EvidenceProvenance, ...]) -> str:
    states = {value.availability for value in values}
    if "payload_conflict" in states:
        return "conflicting"
    providers = {value.provider for value in values}
    if providers == {"api", "local"}:
        return "both"
    return next(iter(states), "unavailable")


def _family_key(source_id: str | None, properties: Any, item_id: str) -> str:
    if source_id:
        return str(source_id)
    corr_id = properties.get("monty:corr_id") if isinstance(properties, dict) else None
    return f"legacy:{corr_id}" if corr_id else f"item:{item_id}"


def _geometry_parts(geometry: Any) -> tuple[str | None, float | None, float | None, bool]:
    if not isinstance(geometry, dict):
        return None, None, None, False
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type != "Point" or not isinstance(coordinates, list) or len(coordinates) < 2:
        return str(geometry_type) if geometry_type else None, None, None, False
    longitude, latitude = coordinates[:2]
    valid = (
        isinstance(longitude, (int, float))
        and not isinstance(longitude, bool)
        and isinstance(latitude, (int, float))
        and not isinstance(latitude, bool)
        and math.isfinite(longitude)
        and math.isfinite(latitude)
        and -180 <= longitude <= 180
        and -90 <= latitude <= 90
    )
    return str(geometry_type), float(longitude) if valid else None, float(latitude) if valid else None, valid


def _asset(assets: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    value = assets.get(key) if isinstance(assets, Mapping) else None
    return value if isinstance(value, Mapping) and isinstance(value.get("href"), str) else None


def footprint_url(assets: Mapping[str, Any]) -> str | None:
    """Unsigned object URL of the PDC 'Maps' asset; signing parameters are dropped."""

    asset = _asset(assets, "Maps")
    if asset is None:
        return None
    parsed = urlparse(asset["href"])
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    return urlunparse(parsed._replace(query="", fragment=""))


def report_url(assets: Mapping[str, Any]) -> str | None:
    """PDC HazardBrief link with any credential-like query values removed."""

    asset = _asset(assets, "report")
    if asset is None:
        return None
    parsed = urlparse(asset["href"])
    if parsed.scheme != "https":
        return None
    query = [(key, value) for key, value in parse_qsl(parsed.query) if not any(part in key.lower() for part in _SENSITIVE_QUERY)]
    return urlunparse(parsed._replace(query=urlencode(query), fragment=""))


def currency_quote(description: Any) -> str | None:
    """The PDC description's own capital-exposure wording, quoted verbatim."""

    match = _CURRENCY_QUOTE.search(description) if isinstance(description, str) else None
    return " ".join(match.group(0).split()) if match else None


def _provenance_record(entity_type: str, entity_id: str, value: EvidenceProvenance) -> dict[str, Any]:
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "provider": value.provider,
        "endpoint_or_file": value.endpoint_or_file,
        "retrieved_at": value.retrieved_at,
        "collection": value.collection,
        "item_id": value.item_id,
        "query_fingerprint": value.query_fingerprint,
        "local_fingerprint": value.local_fingerprint,
        "raw_pointer": value.raw_pointer,
        "availability": value.availability,
        "payload_hash": value.payload_hash,
        "correlation_method": value.correlation_method,
        "correlation_status": value.correlation_status,
    }


CATEGORY_STATUS_COLUMNS = (
    "family_key", "source_event_id", "event_title", "hazard_labels",
    "impact_type", "impact_category", "category_label", "category_group",
    "status", "numeric_value", "original_value", "original_unit",
    "impact_item_id", "provider", "availability", "selected_time",
)
_STATUS_KEYS = ["family_key", "impact_type", "impact_category"]


def _selected_impacts(impacts: pd.DataFrame) -> pd.DataFrame:
    """One selected observation per family/type/category under SNAPSHOT_RULE.

    Rows tied at the selected (time, item, position) key that disagree on value
    or unit are flagged as conflicting rather than resolved to one winner.
    """

    columns = [*_STATUS_KEYS, "numeric_value", "original_value", "original_unit", "impact_item_id", "provider", "availability", "selected_time", "_conflict"]
    if impacts.empty:
        return pd.DataFrame(columns=columns)
    data = impacts.assign(
        _time=impacts["snapshot_time"].fillna(impacts["item_datetime"]).fillna(""),
        _item=impacts["impact_item_id"].fillna(""),
        _position=impacts["detail_position"].fillna(-1),
        _evidence=[
            json.dumps([value, unit], ensure_ascii=False, sort_keys=True, default=str)
            for value, unit in zip(impacts["original_value"], impacts["original_unit"], strict=False)
        ],
    )
    order = [*_STATUS_KEYS, "_time", "_item", "_position"]
    data = data.sort_values(order, kind="stable")
    selected_keys = data.groupby(_STATUS_KEYS, sort=False).tail(1)[order]
    tied = data.merge(selected_keys, on=order)
    summary = tied.groupby(_STATUS_KEYS, sort=False).agg(
        _evidence_count=("_evidence", "nunique"),
        _flagged=("availability", lambda values: bool((values == "conflicting").any())),
        provider=("provider", lambda values: ", ".join(sorted(set(values)))),
        availability=("availability", lambda values: ", ".join(sorted(set(values)))),
    ).reset_index()
    chosen = (
        tied.sort_values([*_STATUS_KEYS, "provider", "impact_item_id"], kind="stable")
        .groupby(_STATUS_KEYS, sort=False)
        .head(1)[[*_STATUS_KEYS, "numeric_value", "original_value", "original_unit", "impact_item_id", "snapshot_time", "item_datetime"]]
    )
    selected = chosen.merge(summary, on=_STATUS_KEYS)
    selected["selected_time"] = selected["snapshot_time"].where(selected["snapshot_time"].notna(), selected["item_datetime"])
    selected["_conflict"] = selected["_evidence_count"].gt(1) | selected["_flagged"]
    return selected[columns]


def _category_status_frame(result: QueryResult, families: pd.DataFrame, impacts: pd.DataFrame) -> pd.DataFrame:
    """Status per event family for every valid impact type/category pair."""

    if families.empty:
        return pd.DataFrame(columns=CATEGORY_STATUS_COLUMNS)
    observed = (
        set(zip(impacts["impact_type"], impacts["impact_category"], strict=False))
        if not impacts.empty
        else set()
    )
    categories = ordered_categories(set(CATEGORY_ORDER) | set(result.query.categories) | {category for _, category in observed})
    pairs = []
    for impact_type in result.query.impact_types or ("affected_total",):
        allowed = set(TYPE_CATEGORIES.get(impact_type, categories))
        allowed |= {category for observed_type, category in observed if observed_type == impact_type}
        pairs.extend((impact_type, category) for category in categories if category in allowed)
    grid = families[["family_key", "source_event_id", "event_title", "hazard_labels"]].merge(
        pd.DataFrame(pairs, columns=["impact_type", "impact_category"]), how="cross"
    )
    merged = grid.merge(_selected_impacts(impacts), on=_STATUS_KEYS, how="left")
    retrieved = set(result.query.categories) if result.query.retrieves_impact_detail else set()
    is_retrieved = merged["impact_category"].isin(retrieved)
    has_value = merged["_conflict"].notna()
    # True only where a selected observation is flagged; rows without one are not conflicts.
    conflict = merged["_conflict"].eq(True)
    numeric = pd.to_numeric(merged["numeric_value"], errors="coerce")
    merged["status"] = np.select(
        [~is_retrieved, ~has_value, conflict, numeric.isna(), numeric.eq(0)],
        ["not_retrieved", "missing", "conflicting", "unavailable", "present_zero"],
        default="present_positive",
    )
    blank = ~is_retrieved | ~has_value
    for column in ("numeric_value", "original_value", "original_unit", "impact_item_id", "provider", "availability", "selected_time"):
        merged[column] = merged[column].astype(object).where(~blank, None)
    merged["numeric_value"] = merged["numeric_value"].where(~(conflict | numeric.isna()), None)
    merged["category_label"] = merged["impact_category"].map(category_label)
    merged["category_group"] = merged["impact_category"].map(category_group)
    return merged[list(CATEGORY_STATUS_COLUMNS)].reset_index(drop=True)


def build_analysis_frames(result: QueryResult) -> AnalysisFrames:
    """Build bounded pandas frames without changing original evidence values."""

    family_records = []
    for row in result.event_families:
        family_records.append(
            {
                "family_key": row.family_key,
                "source_event_id": row.source_event_id,
                "event_title": row.title,
                "start_datetime": row.start_datetime,
                "end_datetime": row.end_datetime,
                "country_codes": row.country_codes,
                "hazard_codes": row.hazard_codes,
                "hazard_labels": hazard_groups(row.hazard_codes),
                "snapshot_count": row.snapshot_count,
                "impact_record_count": row.impact_observation_count,
                "geometry_types": row.geometry_types,
                "provider": _joined(row.providers),
                "correlation_status": row.correlation_status,
            }
        )
    families = pd.DataFrame.from_records(family_records)

    event_records = []
    for row in result.event_snapshots:
        properties = row.original_properties
        geometry_type, longitude, latitude, valid = _geometry_parts(row.geometry)
        event_records.append(
            {
                "family_key": _family_key(row.source_event_id, properties, row.event_item_id),
                "event_title": properties.get("title"),
                "src_event_id": row.source_event_id,
                "event_item_id": row.event_item_id,
                "event_datetime": row.item_datetime,
                "start_datetime": row.start_datetime,
                "end_datetime": row.end_datetime,
                "snapshot_time": row.snapshot_time,
                "snapshot_time_method": row.snapshot_time_method,
                "processing_version": properties.get("processing:version"),
                "episode_number": row.episode_number,
                "hazard_codes": tuple(properties.get("monty:hazard_codes") or ()),
                "country_codes": tuple(properties.get("monty:country_codes") or ()),
                "linked_hazard_ids": row.linked_hazard_ids,
                "linked_impact_ids": row.linked_impact_ids,
                "footprint_url": footprint_url(row.assets),
                "report_url": report_url(row.assets),
                "description_currency_quote": currency_quote(properties.get("description")),
                "geometry": row.geometry,
                "geometry_type": geometry_type,
                "longitude": longitude,
                "latitude": latitude,
                "valid_geometry": valid,
                "provider": _providers(row.provenance),
                "availability": _availability(row.provenance),
                "correlation_status": _joined(sorted({value.correlation_status for value in row.provenance if value.correlation_status})),
            }
        )
    events = pd.DataFrame.from_records(event_records)

    hazard_records = []
    for row in result.hazard_snapshots:
        geometry_type, longitude, latitude, valid = _geometry_parts(row.geometry)
        hazard_records.append(
            {
                "family_key": _family_key(row.source_event_id, row.original_properties, row.hazard_item_id),
                "src_event_id": row.source_event_id,
                "event_item_id": row.event_item_id,
                "hazard_item_id": row.hazard_item_id,
                "item_datetime": row.item_datetime,
                "start_datetime": row.start_datetime,
                "end_datetime": row.end_datetime,
                "snapshot_time": row.snapshot_time,
                "snapshot_time_method": row.snapshot_time_method,
                "episode_number": row.episode_number,
                "country_codes": row.country_codes,
                "hazard_codes": row.hazard_codes,
                "hazard_labels": hazard_groups(row.hazard_codes),
                "severity_value": row.severity_value,
                "severity_unit": row.severity_unit,
                "severity_label": row.severity_label,
                "estimate_type": row.estimate_type,
                "geometry": row.geometry,
                "geometry_type": geometry_type,
                "longitude": longitude,
                "latitude": latitude,
                "valid_geometry": valid,
                "provider": _providers(row.provenance),
                "availability": _availability(row.provenance),
            }
        )
    hazards = pd.DataFrame.from_records(hazard_records)

    impact_records = []
    for row in result.impact_observations:
        geometry_type, longitude, latitude, valid = _geometry_parts(row.geometry)
        impact_records.append(
            {
                "family_key": _family_key(row.source_event_id, row.original_properties, row.impact_item_id),
                "src_event_id": row.source_event_id,
                "event_item_id": row.event_item_id,
                "impact_item_id": row.impact_item_id,
                "country_code": row.country_code,
                "impact_type": row.impact_type,
                "impact_category": row.category,
                "category_label": category_label(row.category),
                "category_group": category_group(row.category),
                "original_value": row.original_value,
                "numeric_value": row.numeric_value,
                "original_unit": row.original_unit,
                "estimate_type": row.estimate_type,
                "item_datetime": row.item_datetime,
                "detail_position": row.detail_position,
                "snapshot_time": row.snapshot_time,
                "snapshot_time_method": row.snapshot_time_method,
                "provider": _providers(row.provenance),
                "availability": _availability(row.provenance),
                "geometry": row.geometry,
                "geometry_type": geometry_type,
                "longitude": longitude,
                "latitude": latitude,
                "valid_geometry": valid,
                "processing_version": row.processing_version,
                "raw_pointer": _joined(value.raw_pointer for value in row.provenance if value.raw_pointer),
            }
        )
    impacts = pd.DataFrame.from_records(impact_records)

    observed_categories = set(impacts.get("impact_category", pd.Series(dtype=str)).dropna())
    category_status = _category_status_frame(result, families, impacts)
    demographics = (
        category_status[category_status["impact_category"].isin(("people", *AGE_BAND_CATEGORIES))]
        .assign(
            age_order=lambda frame: frame["impact_category"].map(
                {category: position for position, category in enumerate(("people", *AGE_BAND_CATEGORIES))}
            )
        )
        .sort_values(["family_key", "age_order"], kind="stable")
        .reset_index(drop=True)
        if not category_status.empty
        else category_status.assign(age_order=pd.Series(dtype="int64"))
    )

    geometry_records = []
    for collection, frame, id_column in (
        ("pdc-events", events, "event_item_id"),
        ("pdc-hazards", hazards, "hazard_item_id"),
        ("pdc-impacts", impacts, "impact_item_id"),
    ):
        if frame.empty:
            continue
        for row in frame.drop_duplicates(["family_key", id_column, "provider", "longitude", "latitude"]).itertuples():
            geometry_records.append(
                {
                    "family_key": row.family_key,
                    "collection": collection,
                    "item_id": getattr(row, id_column),
                    "provider": row.provider,
                    "geometry_type": row.geometry_type,
                    "longitude": row.longitude,
                    "latitude": row.latitude,
                    "valid_geometry": bool(row.valid_geometry),
                    "missing_geometry": row.geometry_type is None,
                    "coordinate_key": f"{row.longitude:.8f},{row.latitude:.8f}" if row.valid_geometry else None,
                }
            )
    geometry = pd.DataFrame.from_records(geometry_records)
    if not geometry.empty:
        cloned = geometry.dropna(subset=["coordinate_key"]).groupby(["family_key", "coordinate_key"])["collection"].nunique()
        cloned_keys = set(cloned[cloned >= 2].index)
        geometry["cloned_coordinate"] = [
            (row.family_key, row.coordinate_key) in cloned_keys for row in geometry.itertuples()
        ]

    correlations = pd.DataFrame.from_records(
        {
            "src_event_id": row.source_event_id,
            "event_item_id": row.source_item_id,
            "target_collection": row.target_collection,
            "target_item_id": row.target_item_id,
            "target_src_event_id": row.target_source_event_id,
            "related_href": row.related_href,
            "related_role": row.related_role,
            "link_method": row.method,
            "src_event_id_match": (
                row.source_event_id == row.target_source_event_id
                if row.source_event_id is not None and row.target_source_event_id is not None
                else None
            ),
            "episode_match": row.episode_compatible,
            "time_match": row.time_compatible,
            "correlation_fallback": row.method == "legacy_corr_id_fallback",
            "status": row.status,
            "candidate_count": row.candidate_count,
            "review_reason": row.review_reason,
        }
        for row in result.correlation_evidence
    )

    provenance_records = []
    for rows, entity_type, id_name in (
        (result.event_snapshots, "event_snapshot", "event_item_id"),
        (result.hazard_snapshots, "hazard_snapshot", "hazard_item_id"),
        (result.impact_observations, "impact_observation", "impact_item_id"),
    ):
        for row in rows:
            entity_id = str(getattr(row, id_name))
            provenance_records.extend(_provenance_record(entity_type, entity_id, value) for value in row.provenance)
    provenance = pd.DataFrame.from_records(provenance_records)

    quality_values = dict(result.quality_summary)
    quality_values.update(
        {
            "missing_units": int(impacts["original_unit"].isna().sum()) if not impacts.empty else 0,
            "missing_snapshot_times": int(events["snapshot_time"].isna().sum()) if not events.empty else 0,
            "missing_impact_snapshot_times": int(impacts["snapshot_time"].isna().sum()) if not impacts.empty else 0,
            "derived_event_snapshot_times": int(events["snapshot_time_method"].eq("pdc_item_id_exposure_timestamp").sum()) if not events.empty else 0,
            "derived_impact_snapshot_times": int(impacts["snapshot_time_method"].eq("pdc_item_id_exposure_timestamp").sum()) if not impacts.empty else 0,
            "invalid_geometries": int((~geometry["valid_geometry"] & ~geometry["missing_geometry"]).sum()) if not geometry.empty else 0,
            "cloned_point_records": int(geometry.get("cloned_coordinate", pd.Series(dtype=bool)).sum()) if not geometry.empty else 0,
            "gender_categories_available": int(bool(observed_categories.intersection({"women", "men"}))),
        }
    )
    quality = pd.DataFrame([{"metric": key, "value": value} for key, value in sorted(quality_values.items())])
    return AnalysisFrames(
        query=result.query,
        metadata=result.metadata,
        event_families=families,
        event_snapshots=events,
        hazards=hazards,
        impacts=impacts,
        category_status=category_status,
        demographics=demographics,
        geometry=geometry,
        correlations=correlations,
        provenance=provenance,
        quality=quality,
    )


def temporal_history(frames: AnalysisFrames, family_key: str, category: str) -> pd.DataFrame:
    """Return every retained exposure snapshot with change-point flags."""

    columns = (
        "family_key",
        "impact_item_id",
        "impact_type",
        "impact_category",
        "country_code",
        "exposure_snapshot_time",
        "snapshot_time_method",
        "numeric_value",
        "original_value",
        "original_unit",
        "provider",
        "availability",
        "processing_version",
        "snapshot_conflict",
        "valid_for_change",
        "previous_value",
        "delta",
        "direction",
        "is_change_point",
        "series_key",
    )
    if frames.impacts.empty:
        return pd.DataFrame(columns=columns)
    data = frames.impacts[
        (frames.impacts["family_key"] == family_key)
        & (frames.impacts["impact_category"] == category)
    ].copy()
    if data.empty:
        return pd.DataFrame(columns=columns)
    data["exposure_snapshot_time"] = pd.to_datetime(data["snapshot_time"], errors="coerce", utc=True)
    data["numeric_value"] = pd.to_numeric(data["numeric_value"], errors="coerce")
    data["_unit_key"] = data["original_unit"].map(lambda value: json.dumps(value, sort_keys=True, default=str))
    data["_evidence_key"] = data.apply(
        lambda row: json.dumps([row["original_value"], row["original_unit"]], sort_keys=True, default=str),
        axis=1,
    )
    snapshot_group = ["family_key", "impact_type", "impact_category", "country_code", "exposure_snapshot_time"]
    data["snapshot_conflict"] = (
        data.groupby(snapshot_group, dropna=False)["_evidence_key"].transform("nunique").gt(1)
        | data["availability"].eq("conflicting")
    )
    data["valid_for_change"] = data["exposure_snapshot_time"].notna() & data["numeric_value"].notna() & ~data["snapshot_conflict"]
    data["series_key"] = data.apply(
        lambda row: " | ".join((str(row["provider"] or "unknown"), str(row["country_code"] or "all"), row["_unit_key"])),
        axis=1,
    )
    data["previous_value"] = math.nan
    data["delta"] = math.nan
    data["direction"] = "insufficient"
    data["is_change_point"] = False
    valid = data[data["valid_for_change"]].sort_values(
        ["series_key", "exposure_snapshot_time", "impact_item_id", "detail_position"],
        kind="stable",
    )
    valid = valid.drop_duplicates(["series_key", "exposure_snapshot_time", "numeric_value"], keep="last")
    previous = valid.groupby("series_key", dropna=False)["numeric_value"].shift()
    delta = valid["numeric_value"] - previous
    direction = pd.Series("stable", index=valid.index)
    direction.loc[previous.isna()] = "initial"
    direction.loc[delta.gt(0)] = "increased"
    direction.loc[delta.lt(0)] = "decreased"
    data.loc[valid.index, "previous_value"] = previous
    data.loc[valid.index, "delta"] = delta
    data.loc[valid.index, "direction"] = direction
    data.loc[valid.index, "is_change_point"] = previous.isna() | delta.ne(0)
    return data.sort_values(["exposure_snapshot_time", "impact_item_id", "detail_position"], kind="stable")[list(columns)].reset_index(drop=True)


def temporal_summary(frames: AnalysisFrames, family_key: str, category: str) -> TemporalSummary:
    history = temporal_history(frames, family_key, category)
    valid = history[history["valid_for_change"]].copy()
    if history.empty or valid.empty:
        return TemporalSummary("insufficient", 0, 0, f"Insufficient history: no valid {category_label(category)} exposure snapshot values were retrieved. No forecast is produced.")
    if history["snapshot_conflict"].any():
        return TemporalSummary("insufficient", int(valid["exposure_snapshot_time"].nunique()), int(valid["is_change_point"].sum()), "Insufficient history: conflicting values exist at the same exposure snapshot time. No forecast is produced.")
    if valid["series_key"].nunique() != 1:
        return TemporalSummary("insufficient", int(valid["exposure_snapshot_time"].nunique()), int(valid["is_change_point"].sum()), "Insufficient history: the retained observations use multiple provider, country, or unit series. No forecast is produced.")
    valid = valid.sort_values(["exposure_snapshot_time", "impact_item_id"], kind="stable").drop_duplicates("exposure_snapshot_time", keep="last")
    count = len(valid)
    change_points = int(valid["is_change_point"].sum())
    if count < 2:
        return TemporalSummary("insufficient", count, change_points, f"Insufficient history: only {count} valid {category_label(category)} exposure snapshot was retrieved. No forecast is produced.")
    first, last = float(valid.iloc[0]["numeric_value"]), float(valid.iloc[-1]["numeric_value"])
    if valid["numeric_value"].nunique() == 1:
        status = "stable"
    elif last > first:
        status = "increased"
    elif last < first:
        status = "decreased"
    else:
        status = "changed"
    return TemporalSummary(
        status,
        count,
        change_points,
        f"{category_label(category)} exposure {status} across {count} valid exposure snapshots: {first:,.0f} to {last:,.0f}. This is a historical PDC exposure-snapshot summary, not an observed impact report or forecast.",
    )


def gender_message(frames: AnalysisFrames) -> str | None:
    categories = set(frames.impacts.get("impact_category", pd.Series(dtype=str)).dropna())
    if categories.intersection({"women", "men"}):
        return None
    return "PDC gender-disaggregated categories were not available in the selected evidence."


# --------------------------------------------------------------------------
# Event-level summary: one row per event family for the queried country.
# --------------------------------------------------------------------------

SUMMARY_MEASURES = ("people", "households", "schools", "hospitals", "capital")
SUMMARY_BASE_COLUMNS = (
    "family_key", "source_event_id", "title", "pdc_hazard_type", "hazard_group", "hazard_groups",
    "hazard_codes", "caveat", "countries", "n_countries", "multi_country", "country",
    "event_date", "year", "month", "start", "end", "duration_hours",
    "snapshot_count", "first_snapshot_time", "last_snapshot_time",
    "alert_level_max", "alert_level_latest", "longitude", "latitude", "valid_point",
    "footprint_url", "report_url", "description_currency_quote", "exposure_class",
)
SUMMARY_COLUMNS = SUMMARY_BASE_COLUMNS + tuple(
    f"{measure}_{suffix}"
    for measure in SUMMARY_MEASURES
    for suffix in ("peak", "peak_time", "latest", "latest_time", "changes", "status")
)
AGE_BAND_SHORT_LABELS = {
    "children_0_4": "0–4", "children_5_9": "5–9", "children_10_14": "10–14", "children_15_19": "15–19",
    "adult_20_24": "20–24", "adult_25_29": "25–29", "adult_30_34": "30–34", "adult_35_39": "35–39",
    "adult_40_44": "40–44", "adult_45_49": "45–49", "adult_50_54": "50–54", "adult_55_59": "55–59",
    "adult_60_64": "60–64", "elderly": "65+",
}


def alert_rank(label: Any) -> int:
    """Ordinal PDC alert level (0 when absent or unrecognised)."""

    text = str(label).upper() if isinstance(label, str) else ""
    return ALERT_LEVELS.index(text) + 1 if text in ALERT_LEVELS else 0


def exposure_class(value: Any) -> str | None:
    """Size class for maps and swarms; None when no numeric value exists."""

    if value is None or pd.isna(value):
        return None
    value = float(value)
    if value <= 0:
        return EXPOSURE_CLASSES[0]
    if value < 10_000:
        return EXPOSURE_CLASSES[1]
    if value < 100_000:
        return EXPOSURE_CLASSES[2]
    if value < 1_000_000:
        return EXPOSURE_CLASSES[3]
    return EXPOSURE_CLASSES[4]


def _measure_values(frames: AnalysisFrames) -> dict[str, pd.DataFrame]:
    """Peak/latest/change statistics per family for each summary measure."""

    impacts = frames.impacts
    categories = {MEASURE_CATEGORY[measure] for measure in SUMMARY_MEASURES}
    keys = ["family_key", "impact_category"]
    if impacts.empty:
        return {}
    data = impacts[impacts["impact_category"].isin(categories)].copy()
    if data.empty:
        return {}
    data["value"] = pd.to_numeric(data["numeric_value"], errors="coerce")
    data["_time"] = data["snapshot_time"].fillna(data["item_datetime"])
    rows = data.groupby(keys).size().rename("rows")
    numeric = data.dropna(subset=["value"])
    per_time = numeric.groupby([*keys, "_time"], dropna=False)["value"].agg(["nunique", "max"]).reset_index()
    per_time = per_time.rename(columns={"max": "value"}).sort_values([*keys, "_time"], kind="stable")
    conflict = per_time.groupby(keys)["nunique"].max().gt(1).rename("conflict")
    peak = per_time.loc[per_time.groupby(keys)["value"].idxmax()].set_index(keys)[["value", "_time"]]
    peak.columns = ["peak", "peak_time"]
    latest = per_time.groupby(keys).tail(1).set_index(keys)[["value", "_time"]]
    latest.columns = ["latest", "latest_time"]
    changes = per_time.groupby(keys)["value"].agg(lambda values: int(values.diff().fillna(0).ne(0).sum())).rename("changes")
    stats = pd.concat([rows, conflict, peak, latest, changes], axis=1).reset_index()
    return {category: frame.drop(columns="impact_category").set_index("family_key") for category, frame in stats.groupby("impact_category")}


def event_summary(frames: AnalysisFrames) -> pd.DataFrame:
    """One analysis row per event family, with peak and latest exposure values.

    Values are PDC exposure estimates for the queried country. The peak is the
    maximum across retained snapshots (PEAK_RULE); latest is shown alongside.
    Categories are never added together and exposure is never summed across
    events here.
    """

    families = frames.event_families
    if families.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    events = frames.event_snapshots.copy()
    events["_order"] = events["snapshot_time"].fillna(events["event_datetime"]).fillna("")
    latest_event = events.sort_values(["family_key", "_order", "event_item_id"], kind="stable").groupby("family_key").tail(1).set_index("family_key")
    quotes = events.dropna(subset=["description_currency_quote"]).sort_values("_order").groupby("family_key")["description_currency_quote"].last()

    summary = pd.DataFrame({
        "family_key": families["family_key"],
        "source_event_id": families["source_event_id"],
        "title": families["event_title"],
        "pdc_hazard_type": families["event_title"].map(pdc_hazard_type),
        "hazard_group": families["hazard_codes"].map(hazard_group),
        "hazard_groups": families["hazard_labels"],
        "hazard_codes": families["hazard_codes"],
        "caveat": [code_title_caveat(title, codes) for title, codes in zip(families["event_title"], families["hazard_codes"], strict=False)],
        "countries": families["country_codes"],
    })
    summary["n_countries"] = summary["countries"].map(len)
    summary["multi_country"] = summary["n_countries"].gt(1)
    summary["country"] = frames.query.country_code
    event_dates = pd.to_datetime(events.groupby("family_key")["event_datetime"].min(), errors="coerce", utc=True, format="mixed")
    summary["event_date"] = summary["family_key"].map(event_dates)
    summary["year"] = summary["event_date"].dt.year.astype("Int64")
    summary["month"] = summary["event_date"].dt.month.astype("Int64")
    summary["start"] = pd.to_datetime(families["start_datetime"], errors="coerce", utc=True, format="mixed")
    summary["end"] = pd.to_datetime(families["end_datetime"], errors="coerce", utc=True, format="mixed")
    summary["duration_hours"] = (summary["end"] - summary["start"]).dt.total_seconds() / 3600
    summary["snapshot_count"] = families["snapshot_count"].astype(int)
    snapshot_times = pd.to_datetime(events["snapshot_time"], errors="coerce", utc=True, format="mixed")
    summary["first_snapshot_time"] = summary["family_key"].map(snapshot_times.groupby(events["family_key"]).min())
    summary["last_snapshot_time"] = summary["family_key"].map(snapshot_times.groupby(events["family_key"]).max())

    hazards = frames.hazards
    if not hazards.empty and "severity_label" in hazards:
        ranked = hazards.assign(_rank=hazards["severity_label"].map(alert_rank), _order=hazards["snapshot_time"].fillna(hazards["item_datetime"]).fillna(""))
        ranked = ranked[ranked["_rank"] > 0]
        highest = ranked.groupby("family_key")["_rank"].max().map(lambda rank: ALERT_LEVELS[rank - 1])
        latest_alert = ranked.sort_values(["family_key", "_order"], kind="stable").groupby("family_key")["severity_label"].last().str.upper()
        summary["alert_level_max"] = summary["family_key"].map(highest)
        summary["alert_level_latest"] = summary["family_key"].map(latest_alert)
    else:
        summary["alert_level_max"] = None
        summary["alert_level_latest"] = None

    for column, source in (("longitude", "longitude"), ("latitude", "latitude"), ("valid_point", "valid_geometry"), ("footprint_url", "footprint_url"), ("report_url", "report_url")):
        summary[column] = summary["family_key"].map(latest_event[source]) if source in latest_event else None
    summary["valid_point"] = summary["valid_point"].fillna(False).astype(bool)
    summary["description_currency_quote"] = summary["family_key"].map(quotes)

    stats = _measure_values(frames)
    retrieved = set(frames.query.categories) if frames.query.retrieves_impact_detail else set()
    for measure in SUMMARY_MEASURES:
        category = MEASURE_CATEGORY[measure]
        values = stats.get(category, pd.DataFrame(columns=["rows", "conflict", "peak", "peak_time", "latest", "latest_time", "changes"]))
        mapped = values.reindex(summary["family_key"])
        conflict = mapped["conflict"].eq(True).to_numpy()
        peak = pd.to_numeric(mapped["peak"], errors="coerce").to_numpy()
        summary[f"{measure}_peak"] = np.where(conflict, np.nan, peak)
        summary[f"{measure}_peak_time"] = pd.to_datetime(mapped["peak_time"].to_numpy(), errors="coerce", utc=True, format="mixed")
        summary[f"{measure}_latest"] = np.where(conflict, np.nan, pd.to_numeric(mapped["latest"], errors="coerce").to_numpy())
        summary[f"{measure}_latest_time"] = pd.to_datetime(mapped["latest_time"].to_numpy(), errors="coerce", utc=True, format="mixed")
        summary[f"{measure}_changes"] = pd.to_numeric(mapped["changes"], errors="coerce").fillna(0).astype(int).to_numpy()
        rows = pd.to_numeric(mapped["rows"], errors="coerce").fillna(0).to_numpy()
        summary[f"{measure}_status"] = np.select(
            [
                np.full(len(summary), category not in retrieved),
                rows == 0,
                conflict,
                np.isnan(peak),
                peak == 0,
            ],
            ["not_retrieved", "missing", "conflicting", "unavailable", "present_zero"],
            default="present_positive",
        )
    summary["exposure_class"] = summary["people_peak"].map(exposure_class)
    return summary[list(SUMMARY_COLUMNS)].reset_index(drop=True)


def age_profile(frames: AnalysisFrames, family_key: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Age bands for one event at the snapshot of its peak `people` value.

    Returns the ordered bands (value and share of the band sum) and a summary
    with the separate `people` value, the reconciliation difference, and the
    derived 0–4, 0–14 and 65+ shares. `people` is never added to the bands.
    """

    columns = ["impact_category", "band", "value", "share"]
    info: dict[str, Any] = {"snapshot_time": None, "people": None, "band_sum": None, "bands_available": 0, "difference": None, "difference_pct": None, "under5_share": None, "under15_share": None, "over65_share": None}
    impacts = frames.impacts
    if impacts.empty:
        return pd.DataFrame(columns=columns), info
    data = impacts[(impacts["family_key"] == family_key) & impacts["impact_category"].isin(("people", *AGE_BAND_CATEGORIES))].copy()
    data["value"] = pd.to_numeric(data["numeric_value"], errors="coerce")
    data["_time"] = data["snapshot_time"].fillna(data["item_datetime"]).fillna("")
    bands = data[data["impact_category"].isin(AGE_BAND_CATEGORIES)].dropna(subset=["value"])
    if bands.empty:
        return pd.DataFrame(columns=columns), info
    people = data[(data["impact_category"] == "people")].dropna(subset=["value"])
    band_times = set(bands["_time"])
    if not people.empty:
        peak_rows = people[people["value"] == people["value"].max()].sort_values("_time")
        candidates = [time for time in peak_rows["_time"] if time in band_times]
        time = candidates[-1] if candidates else max(band_times)
    else:
        time = max(band_times)
    at_time = bands[bands["_time"] == time].drop_duplicates("impact_category", keep="last")
    order = {category: position for position, category in enumerate(AGE_BAND_CATEGORIES)}
    at_time = at_time.assign(_order=at_time["impact_category"].map(order)).sort_values("_order")
    band_sum = float(at_time["value"].sum())
    profile = pd.DataFrame({
        "impact_category": at_time["impact_category"].to_numpy(),
        "band": at_time["impact_category"].map(AGE_BAND_SHORT_LABELS).to_numpy(),
        "value": at_time["value"].to_numpy(),
    })
    profile["share"] = profile["value"] / band_sum if band_sum else np.nan
    values = dict(zip(profile["impact_category"], profile["value"], strict=False))
    people_at_time = people[people["_time"] == time]["value"]
    people_value = float(people_at_time.iloc[-1]) if not people_at_time.empty else None
    info.update(
        snapshot_time=time or None,
        people=people_value,
        band_sum=band_sum,
        bands_available=int(len(profile)),
        difference=(band_sum - people_value) if people_value is not None else None,
        difference_pct=((band_sum - people_value) * 100 / people_value) if people_value else None,
    )
    if band_sum and len(profile) == len(AGE_BAND_CATEGORIES):
        info["under5_share"] = values["children_0_4"] / band_sum
        info["under15_share"] = (values["children_0_4"] + values["children_5_9"] + values["children_10_14"]) / band_sum
        info["over65_share"] = values["elderly"] / band_sum
    return profile, info


def age_shares(frames: AnalysisFrames) -> pd.DataFrame:
    """Age-band shares per event family, at one snapshot per family.

    The snapshot is the one with a complete set of age bands and the largest
    age-band total (normally the peak). ``share`` = band value / age-band total
    of that snapshot. ``people`` is never added to the bands.
    """

    columns = ["family_key", "band", "band_order", "value", "share", "snapshot_time"]
    impacts = frames.impacts
    if impacts.empty:
        return pd.DataFrame(columns=columns)
    bands = impacts[impacts["impact_category"].isin(AGE_BAND_CATEGORIES)].copy()
    bands["value"] = pd.to_numeric(bands["numeric_value"], errors="coerce")
    bands = bands.dropna(subset=["value"])
    if bands.empty:
        return pd.DataFrame(columns=columns)
    bands["_time"] = bands["snapshot_time"].fillna(bands["item_datetime"]).fillna("")
    bands = bands.drop_duplicates(["family_key", "_time", "impact_category"], keep="last")
    sets = bands.groupby(["family_key", "_time"]).agg(bands_present=("impact_category", "nunique"), total=("value", "sum")).reset_index()
    sets = sets[(sets["bands_present"] == len(AGE_BAND_CATEGORIES)) & (sets["total"] > 0)]
    if sets.empty:
        return pd.DataFrame(columns=columns)
    chosen = sets.sort_values(["family_key", "total", "_time"]).groupby("family_key").tail(1)
    data = bands.merge(chosen[["family_key", "_time", "total"]], on=["family_key", "_time"])
    order = {category: position for position, category in enumerate(AGE_BAND_CATEGORIES)}
    return pd.DataFrame({
        "family_key": data["family_key"],
        "band": data["impact_category"].map(AGE_BAND_SHORT_LABELS),
        "band_order": data["impact_category"].map(order),
        "value": data["value"],
        "share": data["value"] / data["total"],
        "snapshot_time": data["_time"],
    }).sort_values(["family_key", "band_order"]).reset_index(drop=True)


def exceedance(summary: pd.DataFrame, measure: str = "people", by: str = "hazard_group") -> pd.DataFrame:
    """Count of distinct events whose peak value is at least each threshold.

    Zero and missing values are excluded because the chart uses a log axis;
    callers report those counts separately.
    """

    column = f"{measure}_peak"
    if summary.empty or column not in summary:
        return pd.DataFrame(columns=[by, "threshold", "events"])
    data = summary[pd.to_numeric(summary[column], errors="coerce").gt(0)]
    rows = []
    for group, frame in data.groupby(by, sort=False):
        values = np.sort(frame[column].to_numpy(dtype=float))[::-1]
        rows.extend({by: group, "threshold": value, "events": rank} for rank, value in enumerate(values, start=1))
    curve = pd.DataFrame(rows, columns=[by, "threshold", "events"])
    # Several events can share one value: the count at that threshold is all of them.
    return curve.groupby([by, "threshold"], as_index=False, sort=False)["events"].max()


def coverage_by_year(frames: AnalysisFrames) -> pd.DataFrame:
    """Event snapshots and distinct families per year of the requested period.

    Montandon's PDC coverage differs strongly between years, so this table is
    shown beside every time-based chart to separate coverage from hazard trends.
    """

    years = list(frames.query.years)
    events = frames.event_snapshots
    if events.empty:
        return pd.DataFrame({"year": years, "event_snapshots": 0, "event_families": 0})
    year = pd.to_datetime(events["event_datetime"], errors="coerce", utc=True, format="mixed").dt.year
    grouped = events.assign(year=year).groupby("year").agg(event_snapshots=("event_item_id", "nunique"), event_families=("family_key", "nunique"))
    return grouped.reindex(years, fill_value=0).rename_axis("year").reset_index()


def _country_rows(summary: pd.DataFrame) -> pd.DataFrame:
    """One row per (event family, associated country); events without a country are dropped."""

    if summary.empty:
        return pd.DataFrame(columns=["family_key", "country_code", "hazard_group", "snapshot_count", "multi_country"])
    rows = summary[["family_key", "countries", "hazard_group", "snapshot_count", "multi_country"]].explode("countries")
    rows = rows.dropna(subset=["countries"]).rename(columns={"countries": "country_code"})
    return rows.drop_duplicates(["family_key", "country_code"])


def country_event_counts(summary: pd.DataFrame, *, top_hazards: int = 3) -> pd.DataFrame:
    """Distinct PDC event families per associated country.

    An event that lists several countries is counted once in each of them, so
    country totals overlap and must not be added to obtain a global total.
    Only event counts are produced: exposure is never summed across events.
    """

    columns = ["country_code", "event_families", "event_snapshots", "multi_country_events", "top_hazards"]
    rows = _country_rows(summary)
    if rows.empty:
        return pd.DataFrame(columns=columns)
    grouped = rows.groupby("country_code")
    counts = pd.DataFrame({
        "event_families": grouped["family_key"].nunique(),
        "event_snapshots": grouped["snapshot_count"].sum().astype(int),
        "multi_country_events": grouped["multi_country"].sum().astype(int),
    })
    hazards = rows.groupby(["country_code", "hazard_group"])["family_key"].nunique().rename("events").reset_index()
    hazards = hazards.sort_values(["country_code", "events", "hazard_group"], ascending=[True, False, True], kind="stable")
    counts["top_hazards"] = hazards.groupby("country_code").apply(
        lambda frame: ", ".join(f"{group} ({events})" for group, events in zip(frame["hazard_group"].head(top_hazards), frame["events"].head(top_hazards), strict=True)),
        include_groups=False,
    )
    counts = counts.reset_index().sort_values(["event_families", "country_code"], ascending=[False, True], kind="stable")
    return counts[columns].reset_index(drop=True)


def combined_country_events(summaries: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Events of several country summaries, once each, with exposure values removed.

    PDC exposure values are estimates for the queried country, so an event
    shared by two queried countries carries two different values; neither is
    picked over the other. Event identity, hazard, alert and location are kept.
    """

    frames = [frame for frame in summaries.values() if not frame.empty]
    if not frames:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    combined = pd.concat(frames, ignore_index=True).drop_duplicates("family_key").reset_index(drop=True)
    for measure in SUMMARY_MEASURES:
        for suffix in ("peak", "latest"):
            combined[f"{measure}_{suffix}"] = np.nan
        combined[f"{measure}_status"] = "not_combined"
    combined["country"] = None
    return combined
