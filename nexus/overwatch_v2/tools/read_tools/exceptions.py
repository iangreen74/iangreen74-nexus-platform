"""Typed exceptions read-tool handlers raise to signal classified failure.

Registry.dispatch() catches these and surfaces them in ToolResult.error
(prefixed with the exception class name). Callers categorize by class name
in the error string. None of these are themselves errors-to-the-user;
they're signals for the reasoner to choose retry/fallback/escalate.
"""


class ToolForbidden(PermissionError):
    """AWS or external API refused the call (AccessDenied, 401, 403)."""


class ToolNotFound(LookupError):
    """The named resource doesn't exist (404, ResourceNotFoundException)."""


class ToolThrottled(RuntimeError):
    """The provider asked us to slow down (429, ThrottlingException)."""


class ToolUnknown(RuntimeError):
    """Catch-all for failures that don't classify into the above buckets."""


def map_boto_error(err: Exception) -> Exception:
    """Map a botocore ClientError to one of the typed exceptions above.

    Falls back to ToolUnknown for codes we don't classify. Caller is
    expected to `raise map_boto_error(e) from e` from inside an except.
    """
    code = ""
    try:
        code = (err.response or {}).get("Error", {}).get("Code", "")  # type: ignore[attr-defined]
    except Exception:
        code = ""
    msg = f"{type(err).__name__}({code or 'no-code'}): {err}"
    if code in {
        "AccessDenied", "AccessDeniedException", "UnauthorizedOperation",
        "AuthFailure", "Forbidden",
    }:
        return ToolForbidden(msg)
    if code in {
        "ResourceNotFoundException", "NoSuchEntity", "NotFound", "404",
        "RoleNotFound", "ValidationError",  # CFN's "stack does not exist"
    }:
        return ToolNotFound(msg)
    if code in {
        "ThrottlingException", "Throttling", "RequestLimitExceeded",
        "TooManyRequestsException", "RateLimitExceeded",
    }:
        return ToolThrottled(msg)
    return ToolUnknown(msg)
