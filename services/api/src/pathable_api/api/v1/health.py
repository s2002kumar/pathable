"""Liveness and readiness endpoints."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import TYPE_CHECKING

from fastapi import APIRouter, Response

from pathable_api.api.deps import DatabaseDep, SettingsDep, WarmupDep
from pathable_api.core.logging import get_logger
from pathable_api.db.health import check_dependencies, is_ready
from pathable_api.geo.gazetteer_store import place_index_versions
from pathable_api.geo.place_text import GAZETTEER_VERSION
from pathable_api.schemas.health import (
    DependencyCheck,
    LivenessResponse,
    PlaceIndexReport,
    ReadinessChecks,
    ReadinessResponse,
)

if TYPE_CHECKING:
    from pathable_api.core.config import Settings
    from pathable_api.db.session import Database

router = APIRouter(prefix="/health", tags=["health"])
logger = get_logger(__name__)

_UNCONFIGURED_DETAIL = "DATABASE_URL is not configured for this instance"


@router.get(
    "/live",
    response_model=LivenessResponse,
    summary="Liveness probe",
    description=(
        "Reports that the process is running and able to serve HTTP. Touches no "
        "dependency, so a database outage never causes an orchestrator to restart "
        "an otherwise healthy instance. Use /health/ready for traffic gating."
    ),
    operation_id="getLiveness",
)
async def liveness(settings: SettingsDep) -> LivenessResponse:
    return LivenessResponse(
        status="ok",
        service=settings.service_name,
        version=settings.app_version,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Reports whether this instance should receive traffic. Probes PostgreSQL "
        "connectivity and PostGIS availability under a bounded timeout, and reports "
        "whether every preloaded routing graph is loaded. Returns 200 when ready and "
        "503 when not; the body shape is identical in both cases. Also reports, without "
        "letting it affect readiness, whether the stored place index is the version this "
        "release reads."
    ),
    operation_id="getReadiness",
    responses={
        HTTPStatus.SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "One or more dependencies are unavailable.",
        }
    },
)
async def readiness(
    settings: SettingsDep,
    database: DatabaseDep,
    warmup: WarmupDep,
    response: Response,
) -> ReadinessResponse:
    if database is None:
        checks = ReadinessChecks(
            database=DependencyCheck(status="unavailable", detail=_UNCONFIGURED_DETAIL),
            postgis=DependencyCheck(status="unavailable", detail=_UNCONFIGURED_DETAIL),
        )
    else:
        checks = await check_dependencies(
            database.engine, timeout_seconds=settings.readiness_timeout_seconds
        )

    # The graph line comes from the lifespan's preload state, not from a probe:
    # a graph that is still loading is a fact about this process, not the database.
    checks = checks.model_copy(update={"graph": warmup.check()})

    ready = is_ready(checks)
    response.status_code = HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if ready else "not_ready",
        service=settings.service_name,
        version=settings.app_version,
        checks=checks,
        place_index=(
            None
            if database is None
            else await _place_index(database, settings, asked=checks.database.status == "ok")
        ),
    )


async def _place_index(database: Database, settings: Settings, *, asked: bool) -> PlaceIndexReport:
    """Whether the stored place index is the one this release reads.

    A release that changes how places are indexed migrates the schema, but the
    index itself is data, restored rather than rebuilt on the pilot. An old
    index under new code still answers, just not as the new code expects, so it
    is said here, where deployment tooling and the smoke test look.
    """

    def report(status: str, detail: str, stored: int | None = None) -> PlaceIndexReport:
        return PlaceIndexReport.model_validate(
            {
                "status": status,
                "detail": detail,
                "stored_version": stored,
                "expected_version": GAZETTEER_VERSION,
            }
        )

    if settings.geocoding_provider != "local":
        return report("not_used", f"place search uses {settings.geocoding_provider}")
    if not asked:
        return report("unknown", "the database could not be asked")
    try:
        async with asyncio.timeout(settings.readiness_timeout_seconds):
            async with database.session() as session:
                versions = await place_index_versions(session)
    except Exception:
        # Readiness never raises; the reason goes to the private log.
        logger.warning("Place index version could not be read", exc_info=True)
        return report("unknown", "the place index could not be read")

    if not versions:
        return report("absent", "no region has a place index")
    described = ", ".join(f"{slug}: version {version}" for slug, version in versions)
    known = [version for _, version in versions if version is not None]
    oldest = min(known) if len(known) == len(versions) else None
    if any(version != GAZETTEER_VERSION for _, version in versions):
        return report(
            "stale", f"{described}; this release reads version {GAZETTEER_VERSION}", oldest
        )
    return report("current", described, oldest)
