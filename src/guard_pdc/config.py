"""Environment-backed configuration for the read-only Montandon provider."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urljoin, urlparse


DEFAULT_ENDPOINT = "https://montandon-eoapi.ifrc.org/stac"


TOKEN_VARIABLE = "MONTANDON_API_TOKEN"


class ConfigError(ValueError):
    """The local provider configuration is missing or unsafe."""


def find_token(start: Path | None = None) -> tuple[str | None, str | None]:
    """Look for the API token without ever showing it.

    The environment variable comes first, then a ``.env`` file in the current
    folder or one of its parents (a notebook usually runs one folder below the
    project root). Returns ``(token, source)``, where ``source`` describes where
    the token came from; both are ``None`` when nothing was found.
    """

    token = os.environ.get(TOKEN_VARIABLE, "").strip()
    if token:
        return token, f"the {TOKEN_VARIABLE} environment variable"
    folder = (start or Path.cwd()).resolve()
    for candidate in (folder, *folder.parents[:3]):
        path = candidate / ".env"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            name, separator, value = line.strip().removeprefix("export ").partition("=")
            value = value.strip().strip("'\"")
            if separator and name.strip() == TOKEN_VARIABLE and value:
                return value, "a .env file"
    return None, None


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
        token = os.environ.get(TOKEN_VARIABLE, "").strip() or None
        if require_token and not token:
            raise ConfigError(f"{TOKEN_VARIABLE} is required for API access")

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
