"""Page markers handed to the client and accepted back from it.

A marker says which page comes next and carries Dataverse's paging cookie. Because it
travels through the model, it is untrusted when it returns. Each marker is signed with a
key that exists only in this server's memory and is bound to the view, filters, and page
size it was issued for, so a marker cannot be altered or reused for a different request.
Markers stop working when the server restarts; the caller simply starts again.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from dataverse_views_mcp.errors import InvalidInputError

MAX_MARKER_LENGTH = 16_000
MAX_PAGE_NUMBER = 100_000

_INVALID = (
    "The page marker is not valid for this request. Call run_view again without a marker "
    "to start from the first page."
)


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def request_binding(view_id: str, filters: list[dict[str, Any]], page_size: int) -> str:
    """A fingerprint of the request a marker belongs to."""
    canonical = json.dumps(
        {"view": view_id, "filters": filters, "size": page_size}, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PageTokens:
    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or secrets.token_bytes(32)

    def _sign(self, payload: bytes) -> bytes:
        return hmac.new(self._key, payload, hashlib.sha256).digest()

    def issue(self, binding: str, page: int, cookie: str | None) -> str:
        payload = json.dumps({"b": binding, "p": page, "c": cookie}, separators=(",", ":")).encode(
            "utf-8"
        )
        return f"{_encode(payload)}.{_encode(self._sign(payload))}"

    def read(self, marker: str, binding: str) -> tuple[int, str | None]:
        """Return (page number, paging cookie), or raise if the marker is not ours."""
        if not isinstance(marker, str) or len(marker) > MAX_MARKER_LENGTH:
            raise InvalidInputError(_INVALID)
        payload_text, separator, signature_text = marker.partition(".")
        try:
            payload = _decode(payload_text)
            signature = _decode(signature_text)
        except (ValueError, UnicodeError):
            raise InvalidInputError(_INVALID) from None
        if not separator or not hmac.compare_digest(signature, self._sign(payload)):
            raise InvalidInputError(_INVALID)
        data = json.loads(payload)
        page, cookie = data.get("p"), data.get("c")
        if (
            data.get("b") != binding
            or not isinstance(page, int)
            or not 1 <= page <= MAX_PAGE_NUMBER
            or not (cookie is None or isinstance(cookie, str))
        ):
            raise InvalidInputError(_INVALID)
        return page, cookie
