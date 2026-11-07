"""Prometheus metrics: what the API did and how long it took, never who asked or where.

Everything an operator needs to see whether the pilot is up and fast: requests
by endpoint and status, how long a route comparison and each route search
took, what place search answered, and how long the routing graph took to load
at startup. Grafana Alloy on the host scrapes it and remote-writes it to
Grafana Cloud (infra/vps/alloy/, docs/deployment/OBSERVABILITY.md).

**Privacy.** A route request is a statement about where somebody is and how they
move, and a search is often an address. So no label carries a coordinate, a
query, a client address, a request id, a segment identity, an exception
message or a raw URL path. Every label value comes from a fixed set written in
this module or in the code it reads — a route template, an allowlisted status
code, a profile key, an outcome — and anything else is folded into one
catch-all value. tests/unit/test_metrics.py proves both properties by sending
those things and searching the exposition for them.

**Bounded.** For the same reason the number of series cannot grow with traffic:
a scanner requesting ten thousand distinct paths adds one ``unmatched`` series,
not ten thousand. That keeps the scrape cheap and the free tier of a hosted
Prometheus within its series limit.

**Never in the way.** Recording a metric must not be able to fail a request.
Every recording method swallows its own exceptions, logs the first one per
method, and carries on; tests/unit/test_metrics.py breaks the collectors and
checks routing and search still answer.

**One process.** Counters live in the process's memory, which is exact because
the pilot runs one API process (ADR 0009). Several workers would each count
their own share and need prometheus_client's multiprocess mode.

The exposition is served on its own port (``METRICS_PORT``), never by the
public application: Caddy routes ``/api/v1/*`` to the API, and nothing routes
to the metrics port, so it is reachable only from inside the host's container
network.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from typing import Concatenate, Final, Literal

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    GCCollector,
    Histogram,
    PlatformCollector,
    ProcessCollector,
    disable_created_metrics,
    start_http_server,
)

from pathable_api.core.logging import get_logger

logger = get_logger(__name__)

# `_created` timestamps double the series a counter or histogram exports and
# nothing on the dashboard reads them.
disable_created_metrics()  # type: ignore[no-untyped-call]  # the library leaves it unannotated

RouteOutcome = Literal["success", "no_route", "invalid", "error"]
PlaceSearchOutcome = Literal["success", "empty", "not_searched", "invalid", "error"]
RouteKind = Literal["shortest", "profile"]
SearchMode = Literal["autocomplete", "submit"]
GraphLoadStage = Literal["preload", "load"]

#: Every label value that is not a closed vocabulary of its own falls back to this.
OTHER: Final = "other"

#: The route label of a request that matched no route in the application.
UNMATCHED_ROUTE: Final = "unmatched"

#: Profile or mode of a request whose body never parsed, so never said which.
UNPARSED: Final = "unparsed"

#: Methods the API answers. Anything else is counted as OTHER.
HTTP_METHODS: Final = frozenset({"GET", "HEAD", "POST", "OPTIONS", "PUT", "PATCH", "DELETE"})

#: Status codes the API sends, kept exact. Any other is counted by class ("4xx").
HTTP_STATUSES: Final = frozenset(
    {200, 201, 204, 304, 400, 401, 403, 404, 405, 409, 413, 415, 422, 429, 500, 502, 503, 504}
)

#: The profile a route request asks for: the request schema's own vocabulary.
ROUTE_PROFILES: Final = frozenset(
    {"wheelchair", "walker", "crutches", "stroller", "reduced_mobility", "custom"}
)

# Buckets, in seconds. A comparison measured 128-137 ms server time on the
# pilot's host and ~460 ms on a paging laptop, so the route buckets are dense
# from 50 ms to 2 s; search answers from an index in milliseconds.
HTTP_BUCKETS: Final = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.5, 5.0, 10.0)
ROUTE_BUCKETS: Final = (0.025, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0)
SEARCH_BUCKETS: Final = (0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


def bounded(value: str, allowed: Collection[str]) -> str:
    """``value`` when it is one of ``allowed``, otherwise :data:`OTHER`."""
    return value if value in allowed else OTHER


def http_method(method: str) -> str:
    return bounded(method.upper(), HTTP_METHODS)


def http_status(status: int) -> str:
    """The exact code when it is one the API sends, otherwise its class."""
    if status in HTTP_STATUSES:
        return str(status)
    return status_class(status)


def status_class(status: int) -> str:
    """``"2xx"`` for 200..299, and so on; :data:`OTHER` outside 100..599."""
    if 100 <= status <= 599:
        return f"{status // 100}xx"
    return OTHER


def _never_raises[**P](
    method: Callable[Concatenate[Metrics, P], None],
) -> Callable[Concatenate[Metrics, P], None]:
    """Make a recording method unable to fail its caller."""

    @functools.wraps(method)
    def guarded(self: Metrics, /, *args: P.args, **kwargs: P.kwargs) -> None:
        try:
            method(self, *args, **kwargs)
        except Exception:
            self._report_failure(method.__name__)

    return guarded


class Metrics:
    """The API's collectors, in a registry of their own.

    Not the library's global registry: each application owns one, so two
    applications in a test process cannot collide over a metric name.
    """

    def __init__(self, *, version: str, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry if registry is not None else CollectorRegistry()
        self._failures: set[str] = set()
        self._failures_lock = threading.Lock()

        # Resident memory, CPU and file descriptors (Linux only; empty elsewhere),
        # the Python version, and garbage collection by generation. The API's
        # memory against its container limit is the number ADR 0009 sized the host on.
        ProcessCollector(registry=self.registry)
        PlatformCollector(registry=self.registry)
        GCCollector(registry=self.registry)

        self.build_info = Gauge(
            "pathable_build_info",
            "Always 1; the version label is the release this process serves.",
            ("version",),
            registry=self.registry,
        )
        self.http_requests = Counter(
            "pathable_http_requests_total",
            "HTTP requests answered, by route template and status. Never by raw path.",
            ("method", "route", "status"),
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "pathable_http_request_duration_seconds",
            "Time from receiving a request to finishing its response.",
            ("method", "route", "status_class"),
            buckets=HTTP_BUCKETS,
            registry=self.registry,
        )
        self.route_comparisons = Counter(
            "pathable_route_comparisons_total",
            "Route comparison requests by profile and outcome. success: a route for the "
            "requested profile was found. no_route: the request was valid but no route "
            "satisfies the profile (the shortest route may still be shown). invalid: the "
            "request could not be routed as given. error: the server could not answer.",
            ("profile", "outcome"),
            registry=self.registry,
        )
        self.route_comparison_duration = Histogram(
            "pathable_route_comparison_duration_seconds",
            "Time to compute a comparison: snapping, both route searches and the explanation.",
            ("profile",),
            buckets=ROUTE_BUCKETS,
            registry=self.registry,
        )
        self.route_search_duration = Histogram(
            "pathable_route_search_duration_seconds",
            "Time one route search took, for each route a comparison found.",
            ("kind",),
            buckets=ROUTE_BUCKETS,
            registry=self.registry,
        )
        self.place_searches = Counter(
            "pathable_place_searches_total",
            "Place search requests by mode and outcome. not_searched: nothing was "
            "searched (no index, or a provider that refuses as-you-type search), which "
            "is not the same answer as empty.",
            ("mode", "outcome"),
            registry=self.registry,
        )
        self.place_search_duration = Histogram(
            "pathable_place_search_duration_seconds",
            "Time the place-search provider took to answer.",
            ("mode",),
            buckets=SEARCH_BUCKETS,
            registry=self.registry,
        )
        self.graph_load = Gauge(
            "pathable_graph_load_seconds",
            "How long this process took to make a region routable at startup. preload: "
            "the whole startup step readiness waits for; load: reading and building the "
            "graph within it.",
            ("region", "stage"),
            registry=self.registry,
        )

        self.build_info.labels(version=version).set(1)

    # ------------------------------------------------------------------
    # Recording. Each method is guarded: it can fail, but never its caller.
    # ------------------------------------------------------------------
    @_never_raises
    def observe_http(self, *, method: str, route: str, status: int, seconds: float) -> None:
        """One answered request. ``route`` must already be a template or UNMATCHED_ROUTE."""
        method_label = http_method(method)
        self.http_requests.labels(
            method=method_label, route=route, status=http_status(status)
        ).inc()
        self.http_duration.labels(
            method=method_label, route=route, status_class=status_class(status)
        ).observe(seconds)

    @_never_raises
    def count_route_comparison(self, *, profile: str, outcome: RouteOutcome) -> None:
        self.route_comparisons.labels(
            profile=_route_profile(profile), outcome=bounded(outcome, _ROUTE_OUTCOMES)
        ).inc()

    @_never_raises
    def observe_route_comparison(
        self, *, profile: str, seconds: float, searches: Iterable[tuple[RouteKind, float]]
    ) -> None:
        """A comparison that ran, and the search time of each route it found."""
        self.route_comparison_duration.labels(profile=_route_profile(profile)).observe(seconds)
        for kind, search_seconds in searches:
            self.route_search_duration.labels(kind=bounded(kind, _ROUTE_KINDS)).observe(
                search_seconds
            )

    @_never_raises
    def count_place_search(self, *, autocomplete: bool | None, outcome: PlaceSearchOutcome) -> None:
        self.place_searches.labels(
            mode=_search_mode(autocomplete), outcome=bounded(outcome, _SEARCH_OUTCOMES)
        ).inc()

    @_never_raises
    def observe_place_search(self, *, autocomplete: bool, seconds: float) -> None:
        self.place_search_duration.labels(mode=_search_mode(autocomplete)).observe(seconds)

    @_never_raises
    def record_graph_load(
        self, *, region: str, preload_seconds: float, load_seconds: float | None
    ) -> None:
        """A region's startup load. ``region`` is a configured, validated slug."""
        self.graph_load.labels(region=region, stage="preload").set(preload_seconds)
        if load_seconds is not None:
            self.graph_load.labels(region=region, stage="load").set(load_seconds)

    def _report_failure(self, method: str) -> None:
        # Logged once per method: a collector that fails on every request would
        # otherwise write one traceback per request.
        with self._failures_lock:
            first = method not in self._failures
            self._failures.add(method)
        if first:
            logger.exception(
                "Recording a metric failed; the request was unaffected",
                extra={"metric_method": method},
            )


_ROUTE_OUTCOMES: Final = frozenset({"success", "no_route", "invalid", "error"})
_SEARCH_OUTCOMES: Final = frozenset({"success", "empty", "not_searched", "invalid", "error"})
_ROUTE_KINDS: Final = frozenset({"shortest", "profile"})


def _route_profile(profile: str) -> str:
    return bounded(profile, ROUTE_PROFILES | {UNPARSED})


def _search_mode(autocomplete: bool | None) -> str:
    if autocomplete is None:
        return UNPARSED
    return "autocomplete" if autocomplete else "submit"


@dataclass(slots=True)
class MetricsServer:
    """The exposition listener, on its own port and its own thread."""

    host: str
    port: int
    _close: Callable[[], None]

    def close(self) -> None:
        self._close()


def serve_metrics(metrics: Metrics, *, host: str, port: int) -> MetricsServer:
    """Serve ``metrics`` in the Prometheus text format on ``host:port``.

    prometheus_client's own threaded WSGI server: it answers every path with the
    exposition and has no other behaviour, so there is nothing on that port to
    secure beyond keeping it off the public internet. Port 0 picks a free port.
    """
    server, thread = start_http_server(port, addr=host, registry=metrics.registry)

    def close() -> None:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    return MetricsServer(host=host, port=server.server_port, _close=close)
