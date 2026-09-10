"""Request-scoped bearer forwarding for authenticated service calls."""

from contextvars import ContextVar

from fastapi import Request

_current_bearer_token: ContextVar[str | None] = ContextVar(
    "niuu_current_bearer_token",
    default=None,
)


def current_bearer_token() -> str | None:
    """Return the bearer token for the current request context, if available."""
    return _current_bearer_token.get()


def extract_bearer_token(request: Request) -> str | None:
    """Extract Bearer token from the Authorization header, or None."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        _current_bearer_token.set(None)
        return None
    token = auth[7:]
    _current_bearer_token.set(token)
    return token
