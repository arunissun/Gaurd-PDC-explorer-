"""Shared PDC evidence retrieval and analysis package."""

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
    ValidationError,
)
from .service import PdcEvidenceService, ServiceError, retrieve

__version__ = "0.1.0"

__all__ = [
    "CorrelationEvidenceRow",
    "EventFamilyRow",
    "EventSnapshotRow",
    "EvidenceProvenance",
    "HazardSnapshotRow",
    "ImpactObservationRow",
    "QueryResult",
    "QuerySpec",
    "RetrievalMetadata",
    "ValidationError",
    "PdcEvidenceService",
    "ServiceError",
    "retrieve",
]
