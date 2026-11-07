"""Route comparison endpoints.

The only endpoint that computes anything is ``POST /routes/compare``. It is a
POST rather than a GET because the request body is structured — two coordinates
and a mobility profile — and because a person's location and their mobility needs
are not something to leave sitting in server access logs and browser history.

Nothing about a request is persisted. See ADR 0007.
"""

from __future__ import annotations

import datetime as dt
import time
from http import HTTPStatus

from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncSession

from pathable_api.api.deps import DatabaseDep
from pathable_api.core.errors import ApiError
from pathable_api.core.logging import get_logger
from pathable_api.core.metrics import Metrics, RouteKind, RouteOutcome
from pathable_api.geo.elevation import elevation_attribution
from pathable_api.geo.models import DatasetVersion
from pathable_api.geo.regions import region_definition
from pathable_api.routing.comparison import RouteComparison, compare_routes
from pathable_api.routing.cost import BlockReason
from pathable_api.routing.engine import DurationEstimate, GradeExtreme, GradientSummary, Route
from pathable_api.routing.graph import GraphRepository, NoActiveDatasetError, RoutableGraph
from pathable_api.routing.profiles import (
    PROFILES,
    ROUTING_POLICY_VERSION,
    SELECTABLE_PROFILE_KEYS,
    MobilityProfile,
    build_custom_profile,
    get_profile,
)
from pathable_api.schemas.errors import ApiErrorResponse
from pathable_api.schemas.routing import (
    CautionModel,
    CostComponentModel,
    DatasetProvenance,
    DifferenceSummaryModel,
    DurationEstimateModel,
    ExplanationModel,
    GradeExtremeModel,
    GradientSummaryModel,
    MobilityProfileListResponse,
    MobilityProfileModel,
    RouteCompareRequest,
    RouteCompareResponse,
    RouteModel,
    RouteSegmentModel,
    RouteUnitModel,
    SnappedPointModel,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/routes", tags=["routing"])

#: ODbL requires visible credit wherever this data is used. Returned with every
#: route so a client cannot display a route without also having the attribution.
OSM_ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0"
SYNTHETIC_ATTRIBUTION = "Synthetic test data — not a survey of any real place."


@router.get(
    "/profiles",
    response_model=MobilityProfileListResponse,
    summary="List mobility profiles",
    description=(
        "The profiles a client may offer, with the hard constraints each one applies. "
        "Constraint values are engineering judgement, not measurements of how people "
        "with these mobility aids actually travel."
    ),
    operation_id="listMobilityProfiles",
)
async def list_profiles() -> MobilityProfileListResponse:
    return MobilityProfileListResponse(
        profiles=[_profile_model(PROFILES[key]) for key in SELECTABLE_PROFILE_KEYS]
    )


def _evidence_age_days(source_timestamp: dt.datetime | None) -> int | None:
    """How old the underlying data is, in whole days.

    Reported against the upstream publication time rather than when PathAble
    fetched it: a dataset downloaded this morning from an extract published two
    years ago is two years old, and saying otherwise would make stale data look
    fresh.
    """
    if source_timestamp is None:
        return None
    age = dt.datetime.now(tz=dt.UTC) - source_timestamp
    return max(0, age.days)


@router.post(
    "/compare",
    response_model=RouteCompareResponse,
    summary="Compare the shortest route with an accessibility-aware route",
    description=(
        "Computes the shortest walking route and a route that respects the chosen "
        "mobility profile, and explains the difference using attributes recorded in "
        "OpenStreetMap. Either route may be absent — a profile with no possible route "
        "is a real answer, and the response says why. No part of the request is stored."
    ),
    operation_id="compareRoutes",
    responses={
        HTTPStatus.NOT_FOUND: {"description": "The region has no active network dataset."},
        HTTPStatus.UNPROCESSABLE_CONTENT: {
            "description": "The request could not be routed as given."
        },
        HTTPStatus.TOO_MANY_REQUESTS: {
            "model": ApiErrorResponse,
            "description": (
                "This client has used its route requests for now (code `rate_limited`). "
                "`Retry-After` says how many seconds until the next is accepted."
            ),
        },
    },
)
async def compare(
    payload: RouteCompareRequest, request: Request, database: DatabaseDep
) -> RouteCompareResponse:
    metrics: Metrics = request.app.state.metrics
    # Anything that escapes without setting an outcome is the server's failure.
    outcome: RouteOutcome = "error"
    try:
        response, outcome = await _compare(payload, request, database, metrics)
        return response
    except ApiError as error:
        outcome = _outcome_of(error, payload.region)
        raise
    finally:
        metrics.count_route_comparison(profile=payload.profile, outcome=outcome)


#: What each refusal the endpoint raises means for the outcome metric.
_REFUSAL_OUTCOMES: dict[str, RouteOutcome] = {
    "no_route": "no_route",
    "custom_profile_required": "invalid",
}


def _outcome_of(error: ApiError, region: str) -> RouteOutcome:
    """The outcome metric's reading of a refusal.

    A refusal not listed above — no database, or a pilot region with nothing
    active — is the server failing a request it should have answered. A region
    nobody defined is the caller's mistake, though the response is the same 404.
    """
    if error.code == "no_active_dataset" and not _is_pilot_region(region):
        return "invalid"
    return _REFUSAL_OUTCOMES.get(error.code, "error")


async def _compare(
    payload: RouteCompareRequest, request: Request, database: DatabaseDep, metrics: Metrics
) -> tuple[RouteCompareResponse, RouteOutcome]:
    if database is None:
        raise ApiError(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            code="database_unavailable",
            message="Routing is unavailable because this instance has no database configured.",
        )

    profile = _resolve_profile(payload)
    repository: GraphRepository = request.app.state.graph_repository

    async with database.session() as session:
        try:
            graph = await repository.active_graph(session, payload.region)
        except NoActiveDatasetError as error:
            raise ApiError(
                status_code=HTTPStatus.NOT_FOUND,
                code="no_active_dataset",
                message=str(error),
            ) from error

        provenance = await dataset_provenance(session, graph, payload.region)

    started = time.perf_counter()
    comparison = compare_routes(
        graph,
        origin=(payload.origin.longitude, payload.origin.latitude),
        destination=(payload.destination.longitude, payload.destination.latitude),
        profile=profile,
    )
    metrics.observe_route_comparison(
        profile=payload.profile,
        seconds=time.perf_counter() - started,
        searches=_search_times(comparison),
    )

    if comparison.standard_route is None and comparison.accessible_route is None:
        raise ApiError(
            status_code=HTTPStatus.UNPROCESSABLE_CONTENT,
            code="no_route",
            message=comparison.standard_failure
            or comparison.accessible_failure
            or "No route could be computed between these points.",
        )

    # Coordinates are deliberately absent from this log line: a route query is a
    # statement about where somebody is and how they move.
    logger.info(
        "Route comparison served",
        extra={
            "region": payload.region,
            "profile": profile.key,
            "dataset_id": str(provenance.dataset_id),
            "standard_found": comparison.standard_route is not None,
            "accessible_found": comparison.accessible_route is not None,
        },
    )

    # Success means the route that was asked for exists. Every requestable
    # profile is an accessibility profile, so a response that carries only the
    # shortest route — with the reason the profile has none — is a no_route.
    found = comparison.accessible_route is not None or profile.is_standard
    return comparison_response(comparison, provenance), ("success" if found else "no_route")


def _is_pilot_region(slug: str) -> bool:
    try:
        region_definition(slug)
    except KeyError:
        return False
    return True


def _search_times(comparison: RouteComparison) -> list[tuple[RouteKind, float]]:
    """How long the search behind each route the comparison found took."""
    times: list[tuple[RouteKind, float]] = []
    if comparison.standard_route is not None:
        times.append(("shortest", comparison.standard_route.computation_ms / 1000.0))
    if comparison.accessible_route is not None:
        times.append(("profile", comparison.accessible_route.computation_ms / 1000.0))
    return times


async def dataset_provenance(
    session: AsyncSession, graph: RoutableGraph, region: str
) -> DatasetProvenance:
    """Where the network that computed a route came from.

    The provenance of the graph that will compute the route — not a second read
    of "active", which an activation or rollback committed in between would
    answer with a different dataset.
    """
    dataset = await session.get(DatasetVersion, graph.dataset_id)
    assert dataset is not None  # noqa: S101 — a loaded graph's dataset exists

    return DatasetProvenance(
        dataset_id=str(dataset.id),
        region=region,
        checksum=dataset.checksum,
        source_type=dataset.source_type,
        source_name=dataset.source_name,
        acquired_at=dataset.acquired_at.isoformat(),
        source_timestamp=(
            None if dataset.source_timestamp is None else dataset.source_timestamp.isoformat()
        ),
        evidence_age_days=_evidence_age_days(dataset.source_timestamp),
        attribution=(OSM_ATTRIBUTION if dataset.source_type == "osm" else SYNTHETIC_ATTRIBUTION),
        # Grades derived from an elevation model carry their own licence
        # obligation, separate from the map's. It travels with the response so no
        # client can show a gradient without also having the credit.
        elevation_attribution=await elevation_attribution(session, dataset.id),
    )


def _resolve_profile(payload: RouteCompareRequest) -> MobilityProfile:
    if payload.profile != "custom":
        return get_profile(payload.profile)

    options = payload.custom
    if options is None:
        raise ApiError(
            status_code=HTTPStatus.UNPROCESSABLE_CONTENT,
            code="custom_profile_required",
            message="A custom profile requires a `custom` block describing the overrides.",
        )
    return build_custom_profile(
        base=options.base,
        exclude_steps=options.exclude_steps,
        max_incline_percent=options.max_incline_percent,
        min_width_m=options.min_width_m,
        avoid_rough_surface=options.avoid_rough_surface,
    )


def _profile_model(profile: MobilityProfile) -> MobilityProfileModel:
    return MobilityProfileModel(
        key=profile.key,
        display_name=profile.display_name,
        description=profile.description,
        excludes_steps=profile.hard_limits.exclude_steps,
        max_incline_percent=profile.hard_limits.max_incline_percent,
        min_width_m=profile.hard_limits.min_width_m,
        prefers_gradient_under_percent=profile.steep_incline_percent,
        prefers_width_over_m=profile.narrow_width_m,
        hard_requirements=list(profile.hard_limits.describe()),
    )


def comparison_response(
    comparison: RouteComparison, provenance: DatasetProvenance
) -> RouteCompareResponse:
    return RouteCompareResponse(
        profile=comparison.profile.key,
        profile_display_name=comparison.profile.display_name,
        profile_description=comparison.profile.description,
        standard_route=_route_model(
            comparison.standard_route, exclusions=comparison.standard_exclusions
        ),
        accessible_route=_route_model(comparison.accessible_route),
        standard_failure=comparison.standard_failure,
        accessible_failure=comparison.accessible_failure,
        distance_comparable=comparison.distance_comparable,
        extra_distance_m=comparison.extra_distance_m,
        extra_distance_fraction=comparison.extra_distance_fraction,
        summary=DifferenceSummaryModel(
            kind=comparison.summary.kind,
            headline=comparison.summary.headline,
            primary_code=comparison.summary.primary_code,
            primary_basis=comparison.summary.primary_basis,
        ),
        explanations=[
            ExplanationModel(
                code=item.code,
                summary=item.summary,
                basis=item.basis,
                evidence=item.evidence,
                shortest_route_segments=list(item.shortest_route_segments),
            )
            for item in comparison.explanations
        ],
        cautions=[
            CautionModel(code=item.code, summary=item.summary, evidence=item.evidence)
            for item in comparison.cautions
        ],
        dataset=provenance,
        routing_policy_version=ROUTING_POLICY_VERSION,
    )


def _exclusion(exclusions: tuple[BlockReason | None, ...], index: int) -> str | None:
    """The hard limit that rules out segment `index`, if any was reported."""
    reason = exclusions[index] if index < len(exclusions) else None
    return None if reason is None else reason.value


def _extreme_model(extreme: GradeExtreme | None) -> GradeExtremeModel | None:
    if extreme is None:
        return None
    return GradeExtremeModel(
        percent=round(extreme.percent, 2),
        direction=extreme.direction,
        source=extreme.source,
        segment_index=extreme.segment_index,
    )


def _gradient_model(summary: GradientSummary) -> GradientSummaryModel:
    return GradientSummaryModel(
        steepest_uphill=_extreme_model(summary.steepest_uphill),
        steepest_downhill=_extreme_model(summary.steepest_downhill),
        recorded_fraction=round(summary.share(summary.recorded_length_m), 4),
        estimated_fraction=round(summary.share(summary.estimated_length_m), 4),
        unknown_fraction=round(summary.share(summary.unknown_length_m), 4),
    )


def _duration_model(duration: DurationEstimate) -> DurationEstimateModel:
    return DurationEstimateModel(
        low_seconds=round(duration.low_seconds),
        high_seconds=round(duration.high_seconds),
        pace_low_mps=duration.pace.low_mps,
        pace_high_mps=duration.pace.high_mps,
        pace_label=duration.pace.described_as,
    )


def _unit_models(route: Route) -> list[RouteUnitModel]:
    units: list[RouteUnitModel] = []
    for unit in route.units:
        first = route.segments[unit.segment_indexes[0]]
        if unit.kind == "crossing":
            units.append(
                RouteUnitModel(
                    kind="crossing",
                    segment_indexes=list(unit.segment_indexes),
                    kerb=first.kerb,
                    kerb_recorded_ends=first.features.crossing_kerb_ends,
                    crossing_type=first.features.crossing_type,
                )
            )
        else:
            counts = [
                count
                for index in unit.segment_indexes
                if (count := route.segments[index].step_count) is not None
            ]
            units.append(
                RouteUnitModel(
                    kind="stairway",
                    segment_indexes=list(unit.segment_indexes),
                    step_count=max(counts) if counts else None,
                )
            )
    return units


def _route_model(
    route: Route | None, *, exclusions: tuple[BlockReason | None, ...] = ()
) -> RouteModel | None:
    if route is None:
        return None

    return RouteModel(
        profile=route.profile_key,
        profile_display_name=route.profile_display_name,
        distance_m=round(route.distance_m, 1),
        effective_distance_m=round(route.effective_distance_m, 1),
        estimated_duration_seconds=round(route.estimated_duration_seconds),
        duration_estimate=_duration_model(route.duration),
        pace_profile=route.pace_profile_key,
        coordinates=list(route.coordinates),
        segments=[
            RouteSegmentModel(
                edge_identity=segment.edge_identity,
                name=segment.name,
                length_m=round(segment.length_m, 1),
                effective_metres=round(segment.effective_metres, 1),
                coordinates=list(segment.coordinates),
                highway=segment.highway,
                surface=segment.surface,
                surface_class=segment.surface_class,
                smoothness_class=segment.smoothness_class,
                steps=segment.steps,
                step_count=segment.step_count,
                incline_percent=segment.incline_percent,
                derived_grade_percent=segment.derived_grade_percent,
                kerb=segment.kerb,
                is_crossing=segment.is_crossing,
                width_m=segment.width_m,
                unknown_attributes=list(segment.unknown_attributes),
                cost_components=[
                    CostComponentModel(
                        code=component.code,
                        effective_metres=round(component.effective_metres, 1),
                        detail=component.detail,
                    )
                    for component in segment.cost_components
                ],
                excluded_by_profile=_exclusion(exclusions, index),
            )
            for index, segment in enumerate(route.segments)
        ],
        origin=SnappedPointModel(
            longitude=route.origin.longitude,
            latitude=route.origin.latitude,
            distance_m=round(route.origin.distance_m, 1),
        ),
        destination=SnappedPointModel(
            longitude=route.destination.longitude,
            latitude=route.destination.latitude,
            distance_m=round(route.destination.distance_m, 1),
        ),
        stairway_count=route.stairway_count,
        step_count=route.step_count,
        crossing_count=route.crossing_count,
        unknown_kerb_crossing_count=route.unknown_kerb_crossing_count,
        units=_unit_models(route),
        steepest_incline_percent=route.steepest_incline_percent,
        gradient=_gradient_model(route.gradient),
        evidence_coverage={
            name: round(value, 4) for name, value in route.evidence_coverage.items()
        },
        gradient_source=route.gradient_source,
        unknown_data_fraction=round(route.unknown_data_fraction, 3),
        computation_ms=round(route.computation_ms, 2),
    )
