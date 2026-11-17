"""Settings, read once at startup from environment variables or a local `.env` file.

Nothing specific to an organization lives in code: the environment address, identity,
sign-in authority, and approved views are all settings.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from dataverse_views_mcp.errors import ConfigurationError

GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
LOGICAL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_TENANT_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_API_VERSION_RE = re.compile(r"^v\d+\.\d+$")
_THUMBPRINT_RE = re.compile(r"^[0-9A-Fa-f]{40}$")

ENV_PREFIX = "DATAVERSE_"
ENV_FILE_VARIABLE = "DATAVERSE_ENV_FILE"
HARD_MAX_PAGE_SIZE = 250


def _split_csv(value: Any) -> Any:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


_HOST_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_ORIGIN_RE = re.compile(r"^https://([^/:?#@\\\s]+)(?::(\d{1,5}))?/?$")


def _https_origin(value: str, setting: str) -> str:
    """Accept only `https://host[:port]`: no credentials, path, query, or fragment.

    The address is matched against a strict pattern rather than parsed leniently, so
    odd characters (spaces, backslashes, line breaks) cannot produce a different host.
    """
    if not isinstance(value, str) or not value.startswith("https://"):
        raise ValueError(f"{setting} must start with https://")
    match = _ORIGIN_RE.match(value)
    if match is None:
        raise ValueError(
            f"{setting} must be https:// and a host name only, with no path, query, "
            "or sign-in details"
        )
    host, port = match.group(1).lower(), match.group(2)
    if not _HOST_RE.match(host) or (port is not None and not 1 <= int(port) <= 65535):
        raise ValueError(f"{setting} does not contain a valid host name")
    return f"https://{host}" + (f":{port}" if port else "")


class Settings(BaseSettings):
    """All configuration for the server. Field names map to `DATAVERSE_<NAME>` variables."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        # No .env file is read unless DATAVERSE_ENV_FILE names one (see load_settings).
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # Where and how to sign in
    url: str
    tenant_id: str
    client_id: str
    client_secret: SecretStr | None = None
    client_certificate_path: Path | None = None
    client_certificate_thumbprint: str | None = None
    authority_host: str = "https://login.microsoftonline.com"
    api_version: str = "v9.2"

    # What the agent may use
    approved_view_ids: Annotated[frozenset[str], NoDecode] = frozenset()
    allow_all_system_views: bool = False
    searchable_tables: Annotated[frozenset[str], NoDecode] = frozenset()
    extra_filter_columns: Annotated[frozenset[str], NoDecode] = frozenset()

    # Limits
    max_page_size: int = Field(default=50, ge=1, le=HARD_MAX_PAGE_SIZE)
    find_record_limit: int = Field(default=10, ge=1, le=50)
    view_cache_ttl_seconds: int = Field(default=60, ge=0, le=3600)
    http_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    max_retries: int = Field(default=3, ge=0, le=6)
    max_retry_wait_seconds: float = Field(default=30.0, ge=0, le=300)

    # Logging
    audit_log_path: Path | None = None
    audit_log_values: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        return _https_origin(value, "DATAVERSE_URL")

    @field_validator("authority_host")
    @classmethod
    def _check_authority(cls, value: str) -> str:
        return _https_origin(value, "DATAVERSE_AUTHORITY_HOST")

    @field_validator("tenant_id")
    @classmethod
    def _check_tenant(cls, value: str) -> str:
        value = value.strip().lower()
        if not (GUID_RE.match(value) or _TENANT_DOMAIN_RE.match(value)):
            raise ValueError("DATAVERSE_TENANT_ID must be a tenant ID (GUID) or a domain name")
        return value

    @field_validator("client_id")
    @classmethod
    def _check_client_id(cls, value: str) -> str:
        value = value.strip().lower()
        if not GUID_RE.match(value):
            raise ValueError("DATAVERSE_CLIENT_ID must be a GUID")
        return value

    @field_validator("api_version")
    @classmethod
    def _check_api_version(cls, value: str) -> str:
        if not _API_VERSION_RE.match(value):
            raise ValueError("DATAVERSE_API_VERSION must look like v9.2")
        return value

    @field_validator("client_certificate_thumbprint")
    @classmethod
    def _check_thumbprint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.replace(":", "").replace(" ", "")
        if not _THUMBPRINT_RE.match(value):
            raise ValueError(
                "DATAVERSE_CLIENT_CERTIFICATE_THUMBPRINT must be 40 hexadecimal characters"
            )
        return value.upper()

    @field_validator("approved_view_ids", mode="before")
    @classmethod
    def _parse_view_ids(cls, value: Any) -> Any:
        items = _split_csv(value)
        if not isinstance(items, (list, set, frozenset, tuple)):
            return items
        cleaned = [str(item).strip().strip("{}").lower() for item in items]
        for item in cleaned:
            if not GUID_RE.match(item):
                raise ValueError(
                    "DATAVERSE_APPROVED_VIEW_IDS must be a comma-separated list of GUIDs"
                )
        return frozenset(cleaned)

    @field_validator("searchable_tables", mode="before")
    @classmethod
    def _parse_tables(cls, value: Any) -> Any:
        items = _split_csv(value)
        if not isinstance(items, (list, set, frozenset, tuple)):
            return items
        cleaned = [str(item).strip().lower() for item in items]
        for item in cleaned:
            if not LOGICAL_NAME_RE.match(item):
                raise ValueError(
                    "DATAVERSE_SEARCHABLE_TABLES must be comma-separated table logical names"
                )
        return frozenset(cleaned)

    @field_validator("extra_filter_columns", mode="before")
    @classmethod
    def _parse_filter_columns(cls, value: Any) -> Any:
        items = _split_csv(value)
        if not isinstance(items, (list, set, frozenset, tuple)):
            return items
        cleaned = [str(item).strip().lower() for item in items]
        for item in cleaned:
            table, _, column = item.partition(".")
            if not (LOGICAL_NAME_RE.match(table) and LOGICAL_NAME_RE.match(column)):
                raise ValueError(
                    "DATAVERSE_EXTRA_FILTER_COLUMNS must be a comma-separated list of table.column"
                )
        return frozenset(cleaned)

    @model_validator(mode="after")
    def _check_credentials(self) -> Settings:
        has_secret = self.client_secret is not None and self.client_secret.get_secret_value() != ""
        has_cert = self.client_certificate_path is not None
        if has_secret == has_cert:
            raise ValueError(
                "Set exactly one of DATAVERSE_CLIENT_SECRET or DATAVERSE_CLIENT_CERTIFICATE_PATH"
            )
        if has_cert and not self.client_certificate_thumbprint:
            raise ValueError(
                "DATAVERSE_CLIENT_CERTIFICATE_THUMBPRINT is required with a certificate"
            )
        return self

    @property
    def authority(self) -> str:
        """Sign-in authority address for this tenant."""
        return f"{self.authority_host}/{self.tenant_id}"

    @property
    def scope(self) -> str:
        """The token scope for this Dataverse environment."""
        return f"{self.url}/.default"

    @property
    def api_base_url(self) -> str:
        return f"{self.url}/api/data/{self.api_version}/"

    def is_view_approved(self, view_id: str) -> bool:
        return self.allow_all_system_views or view_id in self.approved_view_ids


def load_settings(**overrides: Any) -> Settings:
    """Load and validate settings, or raise ConfigurationError with a plain message.

    The message names the settings at fault and the reason. It never echoes a value,
    so a mistyped secret cannot end up in a log.
    """
    # A settings file is read only when named explicitly. Reading ".env" from whatever
    # folder the server was started in would let a file planted there change settings
    # such as the approved views or the audit log location.
    env_file = os.environ.get(ENV_FILE_VARIABLE, "").strip()
    if env_file and "_env_file" not in overrides:
        if not Path(env_file).is_file():
            raise ConfigurationError(f"{ENV_FILE_VARIABLE} does not point to a readable file")
        overrides["_env_file"] = env_file
    try:
        return Settings(**overrides)
    except ValidationError as exc:
        problems: list[str] = []
        for error in exc.errors(include_input=False, include_url=False):
            location = ".".join(str(part) for part in error["loc"])
            name = f"{ENV_PREFIX}{location.upper()}" if location else "settings"
            if error["type"] == "missing":
                problems.append(f"{name} is required")
            else:
                reason = error["msg"].removeprefix("Value error, ")
                problems.append(reason if reason.startswith(ENV_PREFIX) else f"{name}: {reason}")
        raise ConfigurationError("Invalid configuration: " + "; ".join(problems)) from None
