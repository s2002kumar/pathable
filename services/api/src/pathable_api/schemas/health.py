"""Liveness and readiness response models.

Liveness answers "is this process alive?" and deliberately touches no dependency,
so an orchestrator never restarts a healthy API because the database blipped.
Readiness answers "should this instance receive traffic?" and therefore does check
dependencies.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DependencyState = Literal["ok", "unavailable"]
PlaceIndexState = Literal["current", "stale", "absent", "not_used", "unknown"]


class LivenessResponse(BaseModel):
    """Process is running and able to serve HTTP."""

    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "examples": [{"status": "ok", "service": "pathable-api", "version": "0.1.0"}]
        },
    )

    status: Literal["ok"] = Field(description="Always 'ok'; a non-2xx status signals failure.")
    service: str = Field(description="Service identifier.", examples=["pathable-api"])
    version: str = Field(description="Deployed application version.", examples=["0.1.0"])


class DependencyCheck(BaseModel):
    """Outcome of probing one dependency."""

    model_config = ConfigDict(frozen=True)

    status: DependencyState = Field(description="Whether this dependency is usable.")
    detail: str = Field(
        description=(
            "Short, safe explanation. Never includes credentials, connection strings "
            "or stack traces."
        ),
        examples=["connected"],
    )
    latency_ms: float | None = Field(
        default=None,
        description="Observed probe latency in milliseconds, when measured.",
        examples=[4.21],
    )


class ReadinessChecks(BaseModel):
    """Per-dependency readiness results."""

    model_config = ConfigDict(frozen=True)

    database: DependencyCheck = Field(description="PostgreSQL connectivity.")
    postgis: DependencyCheck = Field(description="PostGIS extension availability.")
    graph: DependencyCheck = Field(
        default=DependencyCheck(
            status="ok", detail="graphs load on first request; no regions configured for preload"
        ),
        description=(
            "Routing-graph state. When GRAPH_PRELOAD_REGIONS is set, 'ok' only once every "
            "configured region's graph is loaded and routable; a region with no active dataset "
            "or a failed load keeps the instance not ready. Without preload, graphs load on "
            "the first request and this check is always 'ok'."
        ),
    )


class PlaceIndexReport(BaseModel):
    """Whether the stored place index is the one this release reads.

    Informational: it never changes ``status``. A stale index degrades search,
    not routing, and taking routing offline over it would trade one failure for
    a worse one. Deployment tooling and the smoke test read it instead.
    """

    model_config = ConfigDict(frozen=True)

    status: PlaceIndexState = Field(
        description=(
            "'current' when every region's index was built by the gazetteer version this "
            "release reads; 'stale' when one was built by an older one; 'absent' when "
            "local search has no index; 'not_used' when search is not local; 'unknown' "
            "when the database could not be asked."
        )
    )
    detail: str = Field(description="Which region, and which versions.")
    stored_version: int | None = Field(
        default=None, description="The oldest gazetteer version among the stored indexes."
    )
    expected_version: int = Field(description="The gazetteer version this release reads.")


class ReadinessResponse(BaseModel):
    """Aggregate readiness verdict.

    Returned with HTTP 200 when ``status`` is ``ready`` and HTTP 503 when it is
    ``not_ready``; the body shape is identical either way so clients parse once.
    """

    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "status": "ready",
                    "service": "pathable-api",
                    "version": "0.1.0",
                    "checks": {
                        "database": {
                            "status": "ok",
                            "detail": "connected",
                            "latency_ms": 4.21,
                        },
                        "postgis": {
                            "status": "ok",
                            "detail": "postgis 3.4.2",
                            "latency_ms": 1.08,
                        },
                        "graph": {
                            "status": "ok",
                            "detail": "waterloo: 155714 nodes, 180554 segments loaded in 21.3 s",
                            "latency_ms": 21300.0,
                        },
                    },
                }
            ]
        },
    )

    status: Literal["ready", "not_ready"] = Field(
        description="'ready' only when every dependency check succeeded."
    )
    service: str = Field(description="Service identifier.", examples=["pathable-api"])
    version: str = Field(description="Deployed application version.", examples=["0.1.0"])
    checks: ReadinessChecks = Field(description="Individual dependency results.")
    place_index: PlaceIndexReport | None = Field(
        default=None,
        description=(
            "Whether the stored place index is the version this release reads. Does not "
            "affect `status`. Null only when this instance has no database configured."
        ),
    )
