"""Audit log: one structured line for every tool call, successful or not.

Each line records when, which tool, which view and filters, who called, where, and the
outcome. It never records what the data contained: no row values, no record names, no
counts, no credentials. If a line cannot be written, the tool call fails rather than
proceed unrecorded.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TextIO
from urllib.parse import urlsplit

from dataverse_views_mcp import __version__
from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.errors import DataverseViewsError
from dataverse_views_mcp.results import clean_text

MAX_LOGGED_TEXT = 200
MAX_LOGGED_ITEMS = 50

# Failures that are the server correctly saying no, as opposed to something breaking.
_REFUSAL_CODES = {
    "view_not_approved",
    "permission_denied",
    "invalid_input",
    "invalid_filter",
    "unsupported_view",
    "user_relative_view",
    "not_found",
    "count_limit_exceeded",
}

# Keys in a tool result that hold how many items were returned. A count_view number is
# derived from the data, so it is deliberately not one of these.
_RESULT_COUNT_KEYS = ("row_count", "match_count", "view_count")


class AuditError(DataverseViewsError):
    """The audit line could not be written, so the call is not allowed to complete."""

    code = "audit_unavailable"


@dataclass(frozen=True)
class Caller:
    """Who made the call. On stdio the client name is self-declared, so it is labeled as
    such; the operating-system user and the service identity are known to the server."""

    client: str = "unknown"
    client_version: str = ""
    request_id: str = ""


def outcome_for(error: BaseException | None) -> tuple[str, str]:
    """Classify a call as (outcome, error code)."""
    if error is None:
        return "success", ""
    if isinstance(error, DataverseViewsError):
        return ("refused" if error.code in _REFUSAL_CODES else "error"), error.code
    return "error", "internal_error"


def _safe(value: Any, depth: int = 0) -> Any:
    """Bound and clean a caller-supplied value before it is logged."""
    if isinstance(value, str):
        return clean_text(value.replace("\n", " "), MAX_LOGGED_TEXT)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth >= 3:
        return "[omitted]"
    if isinstance(value, dict):
        return {
            _safe(str(key)): _safe(item, depth + 1)
            for key, item in list(value.items())[:MAX_LOGGED_ITEMS]
        }
    if isinstance(value, (list, tuple)):
        return [_safe(item, depth + 1) for item in value[:MAX_LOGGED_ITEMS]]
    return "[omitted]"


def _os_user() -> str:
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return "unknown"


class AuditLog:
    def __init__(
        self,
        settings: Settings,
        stream: TextIO | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._stream = stream
        self._clock = clock
        self._lock = threading.Lock()
        self._environment = urlsplit(settings.url).hostname or ""
        self._os_user = _os_user()

    def _mask(self, value: Any) -> Any:
        """With DATAVERSE_AUDIT_LOG_VALUES off, log a fingerprint in place of a value."""
        if self._settings.audit_log_values or value is None:
            return value
        digest = hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
        return f"sha256:{digest[:16]}"

    def _filters(self, filters: Any) -> Any:
        cleaned = _safe(filters)
        if not isinstance(cleaned, list):
            return cleaned
        result = []
        for item in cleaned:
            if isinstance(item, dict) and "value" in item:
                item = {**item, "value": self._mask(item["value"])}
            result.append(item)
        return result

    def record(
        self,
        *,
        tool: str,
        caller: Caller,
        arguments: dict[str, Any],
        result: dict[str, Any] | None,
        error: BaseException | None,
        duration_ms: int,
    ) -> None:
        """Write one line. Raises AuditError if it cannot be written."""
        outcome, error_code = outcome_for(error)
        view = result.get("view") if result else None
        entry: dict[str, Any] = {
            "time": self._clock().isoformat(timespec="milliseconds"),
            "event": "tool_call",
            "event_id": str(uuid.uuid4()),
            "server": "dataverse-views-mcp",
            "server_version": __version__,
            "environment": self._environment,
            "tool": tool,
            "caller": {
                "client_declared": _safe(caller.client),
                "client_version": _safe(caller.client_version),
                "os_user": self._os_user,
                "service_identity": self._settings.client_id,
                "request_id": _safe(caller.request_id),
            },
            "view_id": view["id"] if isinstance(view, dict) else _safe(arguments.get("view_id")),
            "view_name": _safe(view.get("name")) if isinstance(view, dict) else None,
            "table": (
                view.get("table")
                if isinstance(view, dict)
                else (result or {}).get("table") or _safe(arguments.get("table"))
            ),
            "filters": self._filters(
                result["filters_applied"]
                if result and "filters_applied" in result
                else arguments.get("filters")
            ),
            "outcome": outcome,
            "error_code": error_code,
            "duration_ms": duration_ms,
        }
        if "search_text" in arguments:
            entry["search_text"] = self._mask(_safe(arguments["search_text"]))
        if result:
            if "page" in result:
                entry["page"] = result["page"]
                entry["page_size"] = result.get("page_size")
            for key in _RESULT_COUNT_KEYS:
                if key in result:
                    entry["items_returned"] = result[key]
        self._write(json.dumps(entry, ensure_ascii=True, default=str))

    def _write(self, line: str) -> None:
        try:
            with self._lock:
                if self._stream is not None:
                    self._stream.write(line + "\n")
                    self._stream.flush()
                elif self._settings.audit_log_path is not None:
                    # Owner-only permissions; append so earlier lines are never rewritten.
                    descriptor = os.open(
                        self._settings.audit_log_path,
                        os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                        0o600,
                    )
                    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                        handle.write(line + "\n")
                else:
                    sys.stderr.write(line + "\n")
                    sys.stderr.flush()
        except (OSError, ValueError):
            raise AuditError(
                "The audit log could not be written, so the request was not completed. "
                "Tell the administrator."
            ) from None
