"""Address search.

A POST, like route comparison, and for the same reason: "wheelchair-accessible
entrance, 200 King Street" is a statement about a person, and it does not belong
in a URL, an access log or browser history.

By default it searches the pilot region's own place index, read from the same
OpenStreetMap extract as the routing network (ADR 0005). When there is nothing to
search — no provider configured, or no index built for the region — the endpoint
says so plainly rather than returning an empty list, because "we found nothing"
and "we did not look" lead a user to do different things.

The local index may be searched as somebody types (`autocomplete: true`): it is
a query against the application's own database. A third-party geocoder whose
policy forbids that answers such a request with "not searched".
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from pathable_api.core.errors import ApiError
from pathable_api.core.logging import get_logger
from pathable_api.geo.geocoding import MAX_QUERY_LENGTH, MAX_RESULTS, GeocodingError
from pathable_api.geo.regions import region_definition
from pathable_api.schemas.errors import ApiErrorResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/geocode", tags=["geocoding"])


class GeocodeRequest(BaseModel):
    """Look up a place name within a pilot region."""

    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={"examples": [{"region": "waterloo", "query": "Waterloo Public Square"}]},
    )

    region: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")] = Field(
        default="waterloo", description="Pilot region slug; results are restricted to its extent."
    )
    query: Annotated[str, Field(min_length=1, max_length=MAX_QUERY_LENGTH)] = Field(
        description="Place name, building code or address to look for."
    )
    limit: Annotated[int, Field(ge=1, le=MAX_RESULTS)] = MAX_RESULTS
    autocomplete: bool = Field(
        default=False,
        description=(
            "True when the query is still being typed. Only a provider that permits "
            "as-you-type searching answers; any other reports that it did not search."
        ),
    )


class GeocodeMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str = Field(description="Human-readable description, as the provider wrote it.")
    longitude: float
    latitude: float
    category: str | None = Field(
        default=None, description="What the provider called this — a road, a building, a suburb."
    )
    kind: Literal["place", "address", "street"] | None = Field(
        default=None, description="The local index only: what kind of entry this is."
    )
    matched_on: Literal["code", "initialism", "name", "fuzzy"] | None = Field(
        default=None,
        description=(
            "The local index only: how the query matched. 'code' — a short code recorded in "
            "OpenStreetMap, such as DC or E5; 'initialism' — initials PathAble generated from "
            "the name; 'name' — the name or address; 'fuzzy' — the typo-tolerant pass."
        ),
    )


class GeocodeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str = Field(
        description="Which geocoder answered: 'local', 'nominatim', or 'disabled'."
    )
    enabled: bool = Field(
        description=(
            "False when nothing was searched: no geocoder is configured, the region has "
            "no place index yet, or the provider does not allow as-you-type search. An "
            "empty `matches` with `enabled: false` means nothing was searched, not that "
            "nothing was found."
        )
    )
    matches: list[GeocodeMatch]
    attribution: str | None = Field(
        default=None,
        description=(
            "Credit the data's licence requires, and for the local index the date its "
            "OpenStreetMap data is current to."
        ),
    )


@router.post(
    "/search",
    response_model=GeocodeResponse,
    summary="Find coordinates for a place name",
    description=(
        "Searches for a place, building code, address or street within a pilot region's "
        "extent, and returns at most five. With `autocomplete: true` the local index is "
        "searched as somebody types; a provider whose usage policy forbids that reports "
        "that it did not search. Nothing about the request is stored."
    ),
    operation_id="searchPlaces",
    responses={
        HTTPStatus.NOT_FOUND: {"description": "The region does not exist."},
        HTTPStatus.SERVICE_UNAVAILABLE: {"description": "The geocoding provider failed."},
        HTTPStatus.TOO_MANY_REQUESTS: {
            "model": ApiErrorResponse,
            "description": (
                "This client has used its searches for now (code `rate_limited`). "
                "`Retry-After` says how many seconds until the next is accepted."
            ),
        },
    },
)
async def search(payload: GeocodeRequest, request: Request) -> GeocodeResponse:
    provider = request.app.state.geocoder

    try:
        definition = region_definition(payload.region)
    except KeyError as error:
        raise ApiError(
            status_code=HTTPStatus.NOT_FOUND,
            code="unknown_region",
            message=f"Pilot region {payload.region!r} does not exist.",
        ) from error

    try:
        outcome = await provider.search(
            payload.query,
            region=definition,
            limit=payload.limit,
            autocomplete=payload.autocomplete,
        )
    except GeocodingError as error:
        raise ApiError(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            code="geocoding_unavailable",
            message=str(error),
        ) from error

    # The query itself is deliberately absent from this log line.
    logger.info(
        "Place search served",
        extra={
            "region": payload.region,
            "provider": provider.name,
            "searched": outcome.searched,
            "autocomplete": payload.autocomplete,
            "matches": len(outcome.matches),
        },
    )

    return GeocodeResponse(
        provider=provider.name,
        enabled=outcome.searched,
        matches=[
            GeocodeMatch(
                label=result.label,
                longitude=result.longitude,
                latitude=result.latitude,
                category=result.category,
                kind=result.kind,
                matched_on=result.matched_on,
            )
            for result in outcome.matches
        ],
        attribution=outcome.attribution if outcome.searched else None,
    )
