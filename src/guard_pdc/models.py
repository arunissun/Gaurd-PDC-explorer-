"""Validated query and normalized PDC evidence contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .taxonomy import (
    ordered_categories,
    ordered_hazard_codes,
    ordered_impact_types,
    unknown_categories,
    unknown_impact_types,
)


ANALYSIS_MODES = frozenset({"country_detail", "annual_country_overview"})
SOURCE_MODES = frozenset({"best_available", "api_only", "local_only", "compare"})
MIN_YEAR = 2000
MAX_YEAR = 2026
MAX_PERIOD_YEARS = 5
ISO3_PATTERN = re.compile(r"^[A-Z]{3}$")
MONTH_ABBREVIATIONS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
SENSITIVE_QUERY_PARTS = ("token", "secret", "signature", "credential", "accesskey", "authorization")


@lru_cache(maxsize=131_072)
def _canonical_url(text: str) -> str:
    """URL with credential-like query values redacted (cached: links repeat)."""

    parsed = urlparse(text)
    if not parsed.query:
        return text
    query = [
        (key, "<redacted>" if any(part in key.lower() for part in SENSITIVE_QUERY_PARTS) else child)
        for key, child in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunparse(parsed._replace(query=urlencode(query)))


def canonical_payload_hash(value: Any) -> str:
    """Hash evidence while ignoring ephemeral signed-URL credential values."""

    def normalize(item: Any) -> Any:
        # Concrete-type checks first: JSON payloads are dicts, lists and strings,
        # and the ABC check is comparatively slow on hundreds of nodes per item.
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, str):
            return _canonical_url(item) if "://" in item else item
        if isinstance(item, Mapping):
            return {key: normalize(child) for key, child in item.items()}
        return item

    payload = json.dumps(normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


class ValidationError(ValueError):
    """One or more user-controlled contract fields are invalid."""

    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


def parse_country_codes(value: str) -> tuple[str, ...]:
    """Canonical ISO3 selection for comma/space-separated interface input."""
    if not isinstance(value, str):
        raise ValidationError(("countries must be ISO3 codes separated by commas",))
    countries = tuple(sorted(set(code.upper() for code in re.split(r"[,;\s]+", value.strip()) if code)))
    if any(not ISO3_PATTERN.fullmatch(code) for code in countries):
        raise ValidationError(("countries must be three-letter ISO3 codes, for example PHL, BGD, NPL",))
    return countries


def _string_tuple(value: Sequence[str], field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field_name} must be a sequence, not a string")
    try:
        values = tuple(value)
    except TypeError as exc:
        raise ValueError(f"{field_name} must be a sequence") from exc
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{field_name} must contain non-empty strings")
    return tuple(item.strip() for item in values)


def _months_tuple(value: Sequence[int]) -> tuple[int, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError("months must be a sequence of integers")
    try:
        values = tuple(value)
    except TypeError as exc:
        raise ValueError("months must be a sequence of integers") from exc
    if any(isinstance(item, bool) or not isinstance(item, int) for item in values):
        raise ValueError("months must contain integers")
    return tuple(sorted(set(values)))


@dataclass(frozen=True, slots=True)
class QuerySpec:
    """One deterministic, bounded retrieval request.

    ``year`` is the first calendar year. ``end_year`` optionally extends a
    country-detail request to at most five consecutive years; ``months`` are
    months of the year applied to every year in the period.
    """

    analysis_mode: str
    country_code: str | None
    year: int
    months: tuple[int, ...]
    hazard_codes: tuple[str, ...] = ()
    impact_types: tuple[str, ...] = ("affected_total",)
    categories: tuple[str, ...] = ("people",)
    source_mode: str = "best_available"
    include_zero_values: bool = True
    include_missing_geometry: bool = True
    refresh_api_cache: bool = False
    end_year: int | None = None

    def __post_init__(self) -> None:
        errors: list[str] = []
        analysis_mode = self.analysis_mode.strip() if isinstance(self.analysis_mode, str) else self.analysis_mode
        if analysis_mode not in ANALYSIS_MODES:
            errors.append("analysis_mode must be country_detail or annual_country_overview")

        if self.country_code is None:
            country_code = None
        elif isinstance(self.country_code, str) and self.country_code.strip():
            country_code = self.country_code.strip().upper()
            if not ISO3_PATTERN.fullmatch(country_code):
                errors.append("country_code must be a three-letter ISO3 code")
        elif isinstance(self.country_code, str):
            country_code = None
        else:
            country_code = None
            errors.append("country_code must be a three-letter ISO3 code or None")

        if analysis_mode == "country_detail" and country_code is None:
            errors.append("country_detail requires one country_code")

        if isinstance(self.year, bool) or not isinstance(self.year, int):
            errors.append("year must be an integer")
        elif not MIN_YEAR <= self.year <= MAX_YEAR:
            errors.append(f"year must be between {MIN_YEAR} and {MAX_YEAR}")

        end_year = self.end_year
        if end_year is not None:
            if isinstance(end_year, bool) or not isinstance(end_year, int):
                errors.append("end_year must be an integer or None")
                end_year = None
            elif isinstance(self.year, int) and not isinstance(self.year, bool):
                if not MIN_YEAR <= end_year <= MAX_YEAR:
                    errors.append(f"end_year must be between {MIN_YEAR} and {MAX_YEAR}")
                elif end_year < self.year:
                    errors.append("end_year cannot be before year")
                elif end_year - self.year + 1 > MAX_PERIOD_YEARS:
                    errors.append(f"a period can cover at most {MAX_PERIOD_YEARS} consecutive calendar years")
                if end_year == self.year:
                    end_year = None
        if analysis_mode == "annual_country_overview" and end_year is not None:
            errors.append("annual_country_overview covers one calendar year")

        try:
            months = _months_tuple(self.months)
        except ValueError as exc:
            months = ()
            errors.append(str(exc))
        if not months:
            errors.append("months must contain at least one month")
        elif any(month < 1 or month > 12 for month in months):
            errors.append("months must contain values from 1 through 12")

        try:
            hazard_codes = ordered_hazard_codes(_string_tuple(self.hazard_codes, "hazard_codes"))
        except ValueError as exc:
            hazard_codes = ()
            errors.append(str(exc))

        try:
            impact_types_input = _string_tuple(self.impact_types, "impact_types")
            impact_types = ordered_impact_types(impact_types_input)
            unknown_types = unknown_impact_types(impact_types)
            if not impact_types:
                errors.append("impact_types must contain at least one value")
            if unknown_types:
                errors.append(f"unsupported impact_types: {', '.join(unknown_types)}")
        except ValueError as exc:
            impact_types = ()
            errors.append(str(exc))

        try:
            categories_input = _string_tuple(self.categories, "categories")
            categories = ordered_categories(categories_input)
            unknown = unknown_categories(categories)
            if not categories:
                errors.append("categories must contain at least one value")
            if unknown:
                errors.append(f"unsupported categories: {', '.join(unknown)}")
        except ValueError as exc:
            categories = ()
            errors.append(str(exc))

        source_mode = self.source_mode.strip() if isinstance(self.source_mode, str) else self.source_mode
        if source_mode not in SOURCE_MODES:
            errors.append("source_mode must be best_available, api_only, local_only, or compare")

        for name in ("include_zero_values", "include_missing_geometry", "refresh_api_cache"):
            if not isinstance(getattr(self, name), bool):
                errors.append(f"{name} must be boolean")

        # Annual overview is event/hazard-only. This prevents an unbounded
        # all-country impact request before a provider is ever called.
        if analysis_mode == "annual_country_overview" and (
            impact_types != ("affected_total",) or categories != ("people",)
        ):
            errors.append("annual_country_overview does not retrieve impact details")

        if errors:
            raise ValidationError(errors)

        object.__setattr__(self, "analysis_mode", analysis_mode)
        object.__setattr__(self, "country_code", country_code)
        object.__setattr__(self, "months", months)
        object.__setattr__(self, "hazard_codes", hazard_codes)
        object.__setattr__(self, "impact_types", impact_types)
        object.__setattr__(self, "categories", categories)
        object.__setattr__(self, "source_mode", source_mode)
        object.__setattr__(self, "end_year", end_year)

    @property
    def last_year(self) -> int:
        return self.end_year if self.end_year is not None else self.year

    @property
    def years(self) -> tuple[int, ...]:
        return tuple(range(self.year, self.last_year + 1))

    def windows(self) -> tuple[tuple[int, int], ...]:
        """Every (year, month) retrieval window in chronological order."""

        return tuple((year, month) for year in self.years for month in self.months)

    @property
    def period_label(self) -> str:
        """Compact human label, e.g. '2021–2025 · Jan–Dec' or '2024 · Jan, Mar'."""

        years = str(self.year) if self.end_year is None else f"{self.year}–{self.end_year}"
        months = self.months
        if months == tuple(range(1, 13)):
            month_text = "Jan–Dec"
        elif len(months) > 2 and months == tuple(range(months[0], months[-1] + 1)):
            month_text = f"{MONTH_ABBREVIATIONS[months[0] - 1]}–{MONTH_ABBREVIATIONS[months[-1] - 1]}"
        else:
            month_text = ", ".join(MONTH_ABBREVIATIONS[month - 1] for month in months)
        return f"{years} · {month_text}"

    @property
    def is_partial_year(self) -> bool:
        return MAX_YEAR in self.years

    @property
    def warnings(self) -> tuple[str, ...]:
        if self.is_partial_year:
            return (f"{MAX_YEAR} is a partial-year period and may not be complete.",)
        return ()

    @property
    def retrieves_impact_detail(self) -> bool:
        return self.analysis_mode == "country_detail"

    def as_dict(self) -> dict[str, Any]:
        return {
            "analysis_mode": self.analysis_mode,
            "country_code": self.country_code,
            "year": self.year,
            "months": list(self.months),
            "hazard_codes": list(self.hazard_codes),
            "impact_types": list(self.impact_types),
            "categories": list(self.categories),
            "source_mode": self.source_mode,
            "include_zero_values": self.include_zero_values,
            "include_missing_geometry": self.include_missing_geometry,
            "refresh_api_cache": self.refresh_api_cache,
            "end_year": self.end_year,
        }

    def retrieval_dict(self) -> dict[str, Any]:
        """Return only fields that define retrieved evidence."""

        values = self.as_dict()
        for name in ("include_zero_values", "include_missing_geometry", "refresh_api_cache"):
            values.pop(name)
        # Single-year requests keep their historical fingerprint.
        if values["end_year"] is None:
            values.pop("end_year")
        return values

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.retrieval_dict(), sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RetrievalMetadata:
    provider: str
    endpoint_or_file: str
    retrieved_at: str
    query_fingerprint: str | None = None
    local_fingerprint: str | None = None
    pages: int = 0
    returned_count: int = 0
    unique_count: int = 0
    complete: bool = False
    server_count: int | None = None
    warnings: tuple[str, ...] = ()
    failed_partitions: tuple[str, ...] = ()
    partition_log: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.pages < 0 or self.returned_count < 0 or self.unique_count < 0:
            raise ValueError("retrieval counts cannot be negative")
        if self.server_count is not None and self.server_count < 0:
            raise ValueError("server_count cannot be negative")


@dataclass(frozen=True, slots=True)
class EvidenceProvenance:
    provider: str
    endpoint_or_file: str
    retrieved_at: str
    collection: str
    item_id: str
    query_fingerprint: str | None = None
    local_fingerprint: str | None = None
    raw_pointer: str | None = None
    payload_hash: str | None = None
    availability: str = "available"
    correlation_method: str | None = None
    correlation_status: str | None = None


@dataclass(frozen=True, slots=True)
class EventFamilyRow:
    family_key: str
    source_event_id: str | None
    title: str | None = None
    start_datetime: str | None = None
    end_datetime: str | None = None
    country_codes: tuple[str, ...] = ()
    hazard_codes: tuple[str, ...] = ()
    snapshot_count: int = 0
    impact_observation_count: int = 0
    geometry_types: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()
    correlation_status: str = "unreviewed"
    provenance: tuple[EvidenceProvenance, ...] = ()


@dataclass(frozen=True, slots=True)
class EventSnapshotRow:
    event_item_id: str
    source_event_id: str | None
    item_datetime: str | None
    episode_number: int | None = None
    start_datetime: str | None = None
    end_datetime: str | None = None
    snapshot_time: str | None = None
    snapshot_time_method: str | None = None
    geometry: Mapping[str, Any] | None = None
    bbox: tuple[float, ...] | None = None
    linked_hazard_ids: tuple[str, ...] = ()
    linked_impact_ids: tuple[str, ...] = ()
    assets: Mapping[str, Any] = field(default_factory=dict)
    original_properties: Mapping[str, Any] = field(default_factory=dict)
    provenance: tuple[EvidenceProvenance, ...] = ()


@dataclass(frozen=True, slots=True)
class HazardSnapshotRow:
    hazard_item_id: str
    event_item_id: str | None
    source_event_id: str | None
    item_datetime: str | None
    episode_number: int | None = None
    start_datetime: str | None = None
    end_datetime: str | None = None
    snapshot_time: str | None = None
    snapshot_time_method: str | None = None
    country_codes: tuple[str, ...] = ()
    hazard_codes: tuple[str, ...] = ()
    geometry: Mapping[str, Any] | None = None
    bbox: tuple[float, ...] | None = None
    severity_value: Any = None
    severity_unit: Any = None
    severity_label: str | None = None
    estimate_type: str | None = None
    original_properties: Mapping[str, Any] = field(default_factory=dict)
    provenance: tuple[EvidenceProvenance, ...] = ()


@dataclass(frozen=True, slots=True)
class ImpactObservationRow:
    impact_item_id: str
    event_item_id: str | None
    source_event_id: str | None
    country_code: str | None
    impact_type: str
    category: str
    original_value: Any
    numeric_value: int | float | None
    original_unit: Any
    estimate_type: str | None
    item_datetime: str | None
    detail_position: int = 0
    snapshot_time: str | None = None
    snapshot_time_method: str | None = None
    geometry: Mapping[str, Any] | None = None
    processing_version: str | None = None
    original_properties: Mapping[str, Any] = field(default_factory=dict)
    provenance: tuple[EvidenceProvenance, ...] = ()


@dataclass(frozen=True, slots=True)
class CorrelationEvidenceRow:
    source_collection: str
    source_item_id: str
    target_collection: str
    target_item_id: str
    source_event_id: str | None
    target_source_event_id: str | None
    related_href: str | None
    related_role: str | None
    episode_compatible: bool | None
    time_compatible: bool | None
    method: str
    status: str
    candidate_count: int = 1
    review_reason: str | None = None
    provenance: tuple[EvidenceProvenance, ...] = ()


@dataclass(frozen=True, slots=True)
class QueryResult:
    query: QuerySpec
    metadata: RetrievalMetadata
    event_families: tuple[EventFamilyRow, ...] = ()
    event_snapshots: tuple[EventSnapshotRow, ...] = ()
    hazard_snapshots: tuple[HazardSnapshotRow, ...] = ()
    impact_observations: tuple[ImpactObservationRow, ...] = ()
    correlation_evidence: tuple[CorrelationEvidenceRow, ...] = ()
    provider_metadata: tuple[RetrievalMetadata, ...] = ()
    quality_summary: Mapping[str, int] = field(default_factory=dict)
    # Per-collection API results of this retrieval, kept in memory only so that
    # "retry failed parts" can re-request just the failed windows. Never exported.
    retrieval_state: Mapping[str, Any] | None = field(default=None, compare=False, repr=False)

    @property
    def complete(self) -> bool:
        return self.metadata.complete and not self.metadata.failed_partitions
