"""One shared token in front of a hosted backend.

Why a token at all: on a Mac on the venue Wi-Fi nobody else could reach the
socket, so it was open. Hosted on the internet, one container per tester, the
address is public -- and the glasses socket carries a live camera, the API
carries that person's day. So each container gets its own ``ACCESS_TOKEN``,
and everything but the health probe asks for it.

Why it stays optional: with ``ACCESS_TOKEN`` empty (the default) nothing here
checks anything, so ``localhost`` development and the stage demo on the Mac
keep working exactly as they did.

Two ways to present it, on purpose:

* the ``X-Access-Token`` header -- what a program sets (curl, the dashboard's
  fetch, the phone's URLSessionWebSocketTask);
* the ``token`` query parameter -- for the places that cannot set a header:
  an ``<img src>`` tag, and a WebSocket URL pasted into a text field.
"""

from __future__ import annotations

import hmac
import logging
import re
from collections.abc import Mapping

from starlette._utils import get_route_path
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

__all__ = [
    "ACCESS_HEADER",
    "ACCESS_QUERY",
    "OPEN_PATHS",
    "AccessTokenMiddleware",
    "RedactTokenFilter",
    "install_log_redaction",
    "presented_token",
    "token_matches",
]

#: Header name the phone and the dashboard send. Case-insensitive on the wire.
ACCESS_HEADER = "X-Access-Token"
#: Query parameter for image tags and pasted WebSocket URLs.
ACCESS_QUERY = "token"

#: Paths that answer without a token. This response contains process liveness
#: only; detailed status is authenticated because it carries tester state.
OPEN_PATHS = frozenset({"/healthz"})


def token_matches(expected: str | None, presented: str | None) -> bool:
    """True when no token is configured, or when ``presented`` equals it.

    Constant-time comparison: a token checked with ``==`` leaks how many
    leading characters were right through the response time.
    """

    want = (expected or "").strip()
    if not want:
        return True
    got = (presented or "").strip()
    return hmac.compare_digest(want.encode("utf-8"), got.encode("utf-8"))


def presented_token(headers: Mapping[str, str], query: Mapping[str, str]) -> str | None:
    """The token a request carries: the header first, then ``?token=``.

    ``headers`` is Starlette's case-insensitive mapping, so one lookup covers
    ``x-access-token`` as proxies and HTTP/2 lower-case it.
    """

    return headers.get(ACCESS_HEADER) or query.get(ACCESS_QUERY)


class AccessTokenMiddleware:
    """Reject HTTP requests that lack the access token with a 401.

    Plain ASGI rather than ``BaseHTTPMiddleware`` so it adds nothing to the
    request path when the token is off. WebSockets pass through untouched: the
    glasses socket checks the token itself, because a socket has to be
    accepted before it can be closed with a code the phone can read (4401),
    and that belongs next to the handler, not in a generic layer.
    """

    def __init__(self, app: ASGIApp, *, token: str | None) -> None:
        self.app = app
        self.token = (token or "").strip()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.token or scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # CORS preflight carries no custom headers by definition; the CORS
        # layer answers it, and the real request that follows is checked.
        # get_route_path, not scope["path"]: behind the proxy uvicorn puts the
        # public prefix back in front (/t/alice/api/status), and the open list
        # is written in route terms.
        if scope.get("method") == "OPTIONS" or get_route_path(scope) in OPEN_PATHS:
            await self.app(scope, receive, send)
            return
        conn = HTTPConnection(scope)
        if token_matches(self.token, presented_token(conn.headers, conn.query_params)):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            status_code=401,
            content={"detail": f"missing or wrong {ACCESS_HEADER} (or ?{ACCESS_QUERY}=)"},
        )
        await response(scope, receive, send)


_TOKEN_IN_URL = re.compile(r"(?i)\b(token=)[^&\s\"']+")


class RedactTokenFilter(logging.Filter):
    """Blank ``?token=...`` out of log lines.

    uvicorn logs every request path with its query string, and the phone's
    socket URL carries the token in it. Container logs get tailed on stage
    and pasted into chats; the secret must not ride along.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _TOKEN_IN_URL.sub(r"\1***", record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TOKEN_IN_URL.sub(r"\1***", a) if isinstance(a, str) else a
                for a in record.args
            )
        return True


def install_log_redaction() -> None:
    """Attach the filter to uvicorn's loggers. Call after uvicorn has
    configured logging (it rebuilds handlers, but keeps logger filters)."""

    for name in ("uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactTokenFilter) for f in logger.filters):
            logger.addFilter(RedactTokenFilter())
