"""Request logging (S1-05): one safe log line per request, and a request id to follow it by.

What is logged: method, path, status, how long it took, the signed-in person's stable id (never
their e-mail) and the project in the path. What is never logged: the query string (it can carry
e-mail addresses or search text), request or response bodies (they hold questions, answers and
document text), and headers (they hold the sign-in token).
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from pharma_ai.core.logging_config import request_id_var

logger = logging.getLogger("pharma_ai.request")

REQUEST_ID_HEADER = "X-Request-ID"
# A caller may supply their own id so a request can be followed across systems. Anything else is
# replaced, so a caller cannot put odd characters or a very long string into the logs.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
# Container health checks fire every few seconds; they are logged at DEBUG to keep logs readable.
_PROBE_PATHS = frozenset({"/health", "/ready"})


def _level_for(status_code: int, path: str) -> int:
    if status_code >= 500:
        return logging.ERROR
    if path in _PROBE_PATHS:
        return logging.DEBUG
    return logging.WARNING if status_code >= 400 else logging.INFO


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = supplied if _VALID_REQUEST_ID.match(supplied) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                self._log(request, 500, started, exc_info=True)
                raise
            response.headers[REQUEST_ID_HEADER] = request_id
            self._log(request, response.status_code, started)
            return response
        finally:
            request_id_var.reset(token)

    @staticmethod
    def _log(request: Request, status_code: int, started: float, *, exc_info: bool = False) -> None:
        # The person is known only if the route authenticated them (request.state.principal).
        principal = getattr(request.state, "principal", None)
        path = request.url.path
        logger.log(
            _level_for(status_code, path),
            "request completed" if not exc_info else "request failed",
            exc_info=exc_info,
            extra={
                "method": request.method,
                "path": path,
                "status": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "actor_sub": getattr(principal, "subject", None),
                "project_id": request.path_params.get("project_id"),
            },
        )
