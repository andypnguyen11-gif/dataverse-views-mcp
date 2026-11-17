"""Error types shared by every module.

Each error carries a short, plain message that is safe to relay to an MCP client and an
end user. Messages never include tokens, tenant details, internal URLs, stack traces, or
text returned by Dataverse. Diagnostic detail goes to the server log (stderr) instead.
"""

from __future__ import annotations


class DataverseViewsError(Exception):
    """Base class. `code` is a stable identifier for logs and clients."""

    code = "error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigurationError(DataverseViewsError):
    """A required setting is missing or malformed. Raised at startup."""

    code = "configuration_error"


class AuthenticationError(DataverseViewsError):
    """The server could not sign in to Dataverse."""

    code = "authentication_failed"


class PermissionDeniedError(DataverseViewsError):
    """The server's identity is not allowed to read the requested data."""

    code = "permission_denied"


class NotFoundError(DataverseViewsError):
    """The requested view, table, or record does not exist."""

    code = "not_found"


class ViewNotApprovedError(DataverseViewsError):
    """The view exists or may exist, but is not on the approved list."""

    code = "view_not_approved"


class InvalidInputError(DataverseViewsError):
    """A tool argument is malformed (bad ID, bad table name, bad page marker)."""

    code = "invalid_input"


class InvalidFilterError(InvalidInputError):
    """A structured filter failed validation."""

    code = "invalid_filter"


class UnsupportedViewError(DataverseViewsError):
    """The view's stored query cannot be run safely by this server."""

    code = "unsupported_view"


class UserRelativeViewError(UnsupportedViewError):
    """The view's results depend on who is asking, so the service identity cannot run it."""

    code = "user_relative_view"


class CountLimitExceededError(DataverseViewsError):
    """The count spans more records than Dataverse allows in one aggregate query."""

    code = "count_limit_exceeded"


class BadRequestError(DataverseViewsError):
    """Dataverse rejected the query the server built."""

    code = "bad_request"


class ThrottledError(DataverseViewsError):
    """Dataverse is rate-limiting the server and retries were exhausted."""

    code = "throttled"


class UpstreamError(DataverseViewsError):
    """Dataverse returned an unexpected response or could not be reached."""

    code = "upstream_error"


class ReadOnlyViolationError(DataverseViewsError):
    """Code attempted a request that could change data. This is a bug, never a user error."""

    code = "read_only_violation"
