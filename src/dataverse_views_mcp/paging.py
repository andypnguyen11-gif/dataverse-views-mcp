"""Page markers handed to the client and accepted back from it.

A marker says which page comes next and carries Dataverse's paging cookie. Because it
travels through the model, it is untrusted when it returns, and it must not be readable
on the way: the cookie holds values of the view's sort columns. Each marker is encrypted
and authenticated with a key that exists only in this server's memory, and is bound to
the view, filters, and page size it was issued for. A marker cannot be read, altered, or
reused for a different request. Markers stop working when the server restarts; the
caller simply starts again.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from dataverse_views_mcp.errors import InvalidInputError

MAX_MARKER_LENGTH = 16_000
MAX_PAGE_NUMBER = 100_000

_INVALID = (
    "The page marker is not valid for this request. Call run_view again without a marker "
    "to start from the first page."
)


def request_binding(view_id: str, filters: list[dict[str, Any]], page_size: int) -> str:
    """A fingerprint of the request a marker belongs to."""
    canonical = json.dumps(
        {"view": view_id, "filters": filters, "size": page_size}, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PageTokens:
    def __init__(self, key: bytes | None = None) -> None:
        self._fernet = Fernet(key or Fernet.generate_key())

    def issue(self, binding: str, page: int, cookie: str | None) -> str:
        payload = json.dumps({"b": binding, "p": page, "c": cookie}, separators=(",", ":"))
        return self._fernet.encrypt(payload.encode("utf-8")).decode("ascii")

    def read(self, marker: str, binding: str) -> tuple[int, str | None]:
        """Return (page number, paging cookie), or raise if the marker is not ours."""
        if not isinstance(marker, str) or not marker or len(marker) > MAX_MARKER_LENGTH:
            raise InvalidInputError(_INVALID)
        try:
            data = json.loads(self._fernet.decrypt(marker.encode("ascii")))
        except (InvalidToken, ValueError, UnicodeError):
            raise InvalidInputError(_INVALID) from None
        page, cookie = data.get("p"), data.get("c")
        if (
            data.get("b") != binding
            or not isinstance(page, int)
            or not 1 <= page <= MAX_PAGE_NUMBER
            or not (cookie is None or isinstance(cookie, str))
        ):
            raise InvalidInputError(_INVALID)
        return page, cookie
