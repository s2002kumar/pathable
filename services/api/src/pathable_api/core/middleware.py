"""HTTP middleware: request metrics, request-size limits, correlation and access logging."""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Final

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route, compile_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pathable_api.core.errors import build_error_response, unhandled_exception_handler
from pathable_api.core.logging import get_logger
from pathable_api.core.metrics import UNMATCHED_ROUTE, Metrics
from pathable_api.core.request_context import (
    REQUEST_ID_HEADER,
    reset_request_id,
    resolve_request_id,
    set_request_id,
)

logger = get_logger(__name__)

#: Largest request body the API will read, in bytes.
#:
#: Every endpoint takes a small fixed structure — two coordinates and a profile
#: name, or a short search string — so 64 KiB is generous by three orders of
#: magnitude. Without a cap, an unauthenticated caller can make the server buffer
#: an arbitrarily large body before validation ever runs, which is a
#: denial-of-service primitive that costs the attacker almost nothing.
MAX_REQUEST_BODY_BYTES = 64 * 1024


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject over-large request bodies before they are read.

    A declared `Content-Length` is checked up front, which stops the common case
    without buffering anything. A chunked request without that header is not
    rejected here: Starlette would have to consume the stream to measure it,
    which is the cost this exists to avoid. Bounding those is the job of whatever
    reverse proxy eventually sits in front of this service, and is recorded in
    the threat model.

    This sits *inside* the correlation middleware, so a 413 carries a request id.
    Correlation costs a UUID and reads no body, and a refusal the caller cannot
    quote in a report is worse than that.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                length = -1
            if length > self._max_bytes:
                logger.warning(
                    "Rejected oversized request body",
                    extra={"declared_bytes": length, "limit_bytes": self._max_bytes},
                )
                return build_error_response(
                    status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    code="request_too_large",
                    message=(
                        f"The request body is larger than the "
                        f"{self._max_bytes // 1024} KiB this API accepts."
                    ),
                )

        return await call_next(request)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a correlation id to the request and log its outcome.

    The id is adopted from an inbound ``X-Request-ID`` header when it is
    well-formed — which lets a future gateway or the frontend correlate a user
    report with server logs — and generated otherwise. It is always echoed back on
    the response, error responses included.

    Unhandled exceptions are converted here rather than being allowed to reach
    Starlette's ``ServerErrorMiddleware``. That middleware sits *outside* this one,
    so by the time it ran, the correlation context would already have been reset
    and the 500 body would carry a null ``request_id`` — precisely the field a user
    needs to quote when reporting the failure.
    """

    def __init__(self, app: ASGIApp, *, log_requests: bool = True) -> None:
        super().__init__(app)
        self._log_requests = log_requests

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        token = set_request_id(request_id)
        started = time.perf_counter()

        try:
            try:
                response = await call_next(request)
            except Exception as exc:
                # Logs the traceback server-side and returns a sanitised body.
                response = await unhandled_exception_handler(request, exc)

            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            response.headers[REQUEST_ID_HEADER] = request_id

            if self._log_requests:
                logger.info(
                    "Request completed",
                    extra={
                        "http_method": request.method,
                        "http_path": request.url.path,
                        "http_status": response.status_code,
                        "duration_ms": duration_ms,
                    },
                )
            return response
        finally:
            reset_request_id(token)


#: The OpenAPI path-item keys that are operations rather than shared fields.
_OPENAPI_METHODS: Final = frozenset({"get", "put", "post", "delete", "options", "head", "patch"})


@dataclass(frozen=True, slots=True)
class RouteTemplate:
    """One path the application serves, and the methods it accepts there."""

    path: str
    pattern: re.Pattern[str]
    methods: frozenset[str]


def route_templates(app: FastAPI) -> tuple[RouteTemplate, ...]:
    """Every path template the application serves, from its own OpenAPI document.

    The document is the API's contract (ADR 0003) and lists each endpoint by its
    full template, whatever routers it was assembled from. The framework's own
    pages — ``/openapi.json``, ``/docs`` — sit outside it and are added from the
    top-level routes.
    """
    templates: list[RouteTemplate] = []
    for path, item in app.openapi().get("paths", {}).items():
        pattern, _, _ = compile_path(path)
        methods = frozenset(key.upper() for key in item if key in _OPENAPI_METHODS)
        templates.append(RouteTemplate(path=path, pattern=pattern, methods=methods))
    documented = {template.path for template in templates}
    for route in app.router.routes:
        if isinstance(route, Route) and route.path not in documented:
            templates.append(
                RouteTemplate(
                    path=route.path,
                    pattern=route.path_regex,
                    methods=frozenset(route.methods or ()),
                )
            )
    return tuple(templates)


def route_template(templates: tuple[RouteTemplate, ...], method: str, path: str) -> str:
    """The template a request matches — ``/api/v1/routes/compare`` — or UNMATCHED_ROUTE.

    Resolved here rather than read from the scope after the call, because a
    request refused before routing (413, 429) never reaches the router, and it
    is exactly those refusals an operator wants counted against their endpoint.
    A method the route does not accept is still that route's template: a 405 is
    a statement about the endpoint, not about an unknown path.
    """
    wrong_method: str | None = None
    for template in templates:
        if not template.pattern.match(path):
            continue
        if method in template.methods or (method == "HEAD" and "GET" in template.methods):
            return template.path
        wrong_method = wrong_method or template.path
    return wrong_method or UNMATCHED_ROUTE


class MetricsMiddleware:
    """Count and time every HTTP request by method, route template and status.

    Plain ASGI rather than ``BaseHTTPMiddleware``: it only has to see the status
    line go past, which needs neither a ``Request`` object nor a second task.
    Registered outermost, so it sees the 413s, 429s and converted 500s the other
    middleware produce, and a request that fails before any response started is
    counted as the 500 Starlette will send for it.
    """

    def __init__(self, app: ASGIApp, *, metrics: Metrics, application: FastAPI) -> None:
        self._app = app
        self._metrics = metrics
        self._application = application
        # Built on the first request, when every router has been included.
        self._templates: tuple[RouteTemplate, ...] | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        started = time.perf_counter()
        status = HTTPStatus.INTERNAL_SERVER_ERROR.value

        async def send_and_watch(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self._app(scope, receive, send_and_watch)
        finally:
            method = str(scope.get("method", ""))
            self._metrics.observe_http(
                method=method,
                route=self._template(method, str(scope.get("path", ""))),
                status=status,
                seconds=time.perf_counter() - started,
            )

    def _template(self, method: str, path: str) -> str:
        try:
            if self._templates is None:
                self._templates = route_templates(self._application)
            return route_template(self._templates, method.upper(), path)
        except Exception:
            # Never let resolving a label fail the request it describes.
            return UNMATCHED_ROUTE
