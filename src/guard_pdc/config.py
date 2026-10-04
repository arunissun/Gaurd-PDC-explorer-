"""Environment-backed configuration for the read-only Montandon provider."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urljoin, urlparse


DEFAULT_ENDPOINT = "https://montandon-eoapi.ifrc.org/stac"


class ConfigError(ValueError):
    """The local provider configuration is missing or unsafe."""


@dataclass(frozen=True, slots=True)
class MontandonConfig:
    endpoint: str = DEFAULT_ENDPOINT
    api_token: str | None = field(default=None, repr=False)
    api_cache_path: Path = Path("data/cache/api")
    manifest_path: Path = Path("data/manifests")
    local_export_path: Path | None = None
    local_index_path: Path = Path("data/index/pdc_local.sqlite")
    timeout: int = 45
    # Concurrent read-only searches; 4 parallel requests ran without throttling
    # in the 2026-10-01 production probe.
    max_workers: int = 4
    # Hosts whose PDC "Maps" assets may be fetched (unsigned, without credentials).
    footprint_hosts: tuple[str, ...] = ("monty-etl-2-minio.ifrc-go.dev.togglecorp.com",)

    def __post_init__(self) -> None:
        endpoint = self.endpoint.rstrip("/")
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ConfigError("endpoint must be an absolute https:// URL")
        if self.timeout < 1:
            raise ConfigError("timeout must be positive")
        if not 1 <= self.max_workers <= 8:
            raise ConfigError("max_workers must be between 1 and 8")
        object.__setattr__(self, "endpoint", endpoint)

    @classmethod
    def from_env(cls, *, require_token: bool = True) -> "MontandonConfig":
        endpoint = os.environ.get("MONTANDON_API_URL", DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT
        token = os.environ.get("MONTANDON_API_TOKEN", "").strip() or None
        if require_token and not token:
            raise ConfigError("MONTANDON_API_TOKEN is required for API access")

        local_value = os.environ.get("PDC_LOCAL_EXPORT_PATH", "").strip()
        cache_value = os.environ.get("PDC_API_CACHE_PATH", "data/cache/api").strip()
        return cls(
            endpoint=endpoint,
            api_token=token,
            api_cache_path=Path(cache_value or "data/cache/api"),
            manifest_path=Path(os.environ.get("PDC_MANIFEST_PATH", "data/manifests")),
            local_export_path=Path(local_value) if local_value else None,
            local_index_path=Path(os.environ.get("PDC_LOCAL_INDEX_PATH", "data/index/pdc_local.sqlite")),
            timeout=int(os.environ.get("PDC_API_TIMEOUT", "45")),
            max_workers=int(os.environ.get("PDC_API_MAX_WORKERS", "4")),
            footprint_hosts=tuple(
                host.strip()
                for host in os.environ.get("PDC_FOOTPRINT_HOSTS", "monty-etl-2-minio.ifrc-go.dev.togglecorp.com").split(",")
                if host.strip()
            ),
        )

    def url(self, path: str) -> str:
        return urljoin(f"{self.endpoint}/", path.lstrip("/"))
