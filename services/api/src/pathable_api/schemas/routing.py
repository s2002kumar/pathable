"""Request and response models for route comparison.

Everything a client can learn about a route is declared here, which means it also
appears in the OpenAPI document and in the generated TypeScript. Three shapes are
deliberate:

* Accessibility values are ``"yes" | "no" | "unknown"`` rather than booleans. A
  boolean cannot express "nobody has recorded this", and a client that receives
  ``false`` will render "no steps" for a segment nobody has ever surveyed.
* ``ml_predictions_used`` is always present and always ``false``. PathAble
  contains no model; stating that explicitly stops a client from inferring
  otherwise from silence, and makes the day it changes a visible change.
* Cost is reported both as real distance and as effective metres, with the
  components that produced it. A route people are asked to trust has to be able
  to show its working.

Gradient is carried twice over, never merged: ``incline_percent`` is what a
mapper recorded and ``derived_grade_percent`` is what the elevation model
suggests. The route-level ``gradient`` summary says which one each figure is.
"""

from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from pathable_api.geo.enums import KerbType, SmoothnessClass, SurfaceClass, TriState
from pathable_api.routing.cost import BlockReason
from pathable_api.routing.profiles import SELECTABLE_PROFILE_KEYS

#: Waterloo sits near (-80.52, 43.47); the ranges here are the WGS84 limits, and
#: region membership is checked separately against the pilot boundary.
Longitude = Annotated[float, Field(ge=-180.0, le=180.0)]
Latitude = Annotated[float, Field(ge=-90.0, le=90.0)]

ProfileKey = Literal["wheelchair", "walker", "crutches", "stroller", "reduced_mobility", "custom"]


def _check_profile_keys_match_registry() -> None:
    """Fail at import if the wire enum and the profile registry disagree.

    The literal above is what generates the client's TypeScript union. A profile
    added to the registry but not here would be unreachable from the frontend,
    and the mismatch would surface as a puzzling 422 rather than a clear error.
    """
    declared = set(get_args(ProfileKey))
    missing = set(SELECTABLE_PROFILE_KEYS) - declared
    if missing:
        msg = f"Mobility profiles missing from the API schema: {', '.join(sorted(missing))}"
        raise RuntimeError(msg)


_check_profile_keys_match_registry()

#: Why the chosen profile rules a segment out. Mirrors `BlockReason`.
ExclusionReason = Literal[
    "foot_prohibited",
    "steps",
    "too_steep",
    "too_narrow",
    "surface_excluded",
    "smoothness_excluded",
]

#: Where a gradient came from: recorded by a mapper, or estimated from terrain.
GradeSource = Literal["osm_incline", "derived_elevation"]

#: What a statement about the route rests on.
EvidenceBasis = Literal["recorded", "estimated", "mixed", "not_recorded", "profile_rule"]


def _check_exclusion_reasons_match_cost_model() -> None:
    """Fail at import if the wire enum drifts from the reasons the cost model gives."""
    declared = set(get_args(ExclusionReason))
    actual = {reason.value for reason in BlockReason}
    if declared != actual:
        msg = f"Exclusion reasons out of step with the cost model: {sorted(declared ^ actual)}"
        raise RuntimeError(msg)


_check_exclusion_reasons_match_cost_model()


class Coordinate(BaseModel):
    """A WGS84 position."""

    model_config = ConfigDict(frozen=True)

    longitude: Longitude = Field(description="Degrees east of the prime meridian.")
    latitude: Latitude = Field(description="Degrees north of the equator.")


class CustomProfileOptions(BaseModel):
    """Overrides for the ``custom`` profile.

    Deliberately narrow. Exposing every cost weight would let a client build a
    model nobody has reasoned about, and the resulting route would still carry
    PathAble's name.
    """

    model_config = ConfigDict(frozen=True)

    base: Literal["wheelchair", "walker", "crutches", "stroller", "reduced_mobility"] = Field(
        default="wheelchair",
        description="Preset the custom profile starts from.",
    )
    exclude_steps: bool | None = Field(default=None, description="Treat stairways as impassable.")
    max_incline_percent: Annotated[float, Field(ge=0.0, le=45.0)] | None = Field(
        default=None,
        description=(
            "Steepest uphill gradient to allow, in the direction of travel. It applies to the "
            "gradient routing uses: recorded in OpenStreetMap where one is, otherwise estimated "
            "from the elevation model. A segment with no gradient at all is never excluded, and "
            "neither is a descent. Repeated back exactly as sent."
        ),
    )
    min_width_m: Annotated[float, Field(ge=0.0, le=5.0)] | None = Field(
        default=None,
        description="Narrowest recorded width to allow. Unrecorded widths are never excluded.",
    )
    avoid_rough_surface: bool | None = Field(
        default=None, description="Treat gravel, dirt and cobblestone as impassable."
    )


class RouteCompareRequest(BaseModel):
    """Ask for the shortest route and an accessibility-aware alternative."""

    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "region": "waterloo",
                    "origin": {"longitude": -80.5449, "latitude": 43.4643},
                    "destination": {"longitude": -80.5204, "latitude": 43.4723},
                    "profile": "wheelchair",
                }
            ]
        },
    )

    region: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")] = Field(
        default="waterloo", description="Pilot region slug."
    )
    origin: Coordinate
    destination: Coordinate
    profile: ProfileKey = Field(
        default="wheelchair", description="Mobility profile for the accessible route."
    )
    custom: CustomProfileOptions | None = Field(
        default=None,
        description="Only used when `profile` is `custom`; ignored otherwise.",
    )


class CostComponentModel(BaseModel):
    """One contribution to a segment's cost."""

    model_config = ConfigDict(frozen=True)

    code: Literal[
        "distance",
        "surface",
        "smoothness",
        "incline",
        "steps",
        "kerb",
        "crossing",
        "width",
        "uncertainty",
    ]
    effective_metres: float = Field(description="Effective metres this contribution added.")
    detail: str = Field(description="Plain-language reason, derived from recorded attributes.")


class RouteSegmentModel(BaseModel):
    """One mapped segment as traversed by a route."""

    model_config = ConfigDict(frozen=True)

    edge_identity: str = Field(description="Stable identity of this segment within the dataset.")
    name: str | None = Field(default=None, description="Street or path name, when OSM records one.")
    length_m: float
    effective_metres: float = Field(
        description="What this segment cost the chosen profile, in effective metres."
    )
    coordinates: list[tuple[float, float]] = Field(
        description="[longitude, latitude] positions, oriented along travel."
    )
    highway: str | None = None
    surface: str | None = Field(default=None, description="Raw OSM surface value, if recorded.")
    surface_class: SurfaceClass
    smoothness_class: SmoothnessClass
    steps: TriState = Field(
        description="Whether this segment is a stairway. `unknown` means nobody has recorded it."
    )
    step_count: int | None = None
    incline_percent: float | None = Field(
        default=None,
        description=(
            "Gradient recorded in OpenStreetMap, signed along travel: positive climbs. Null "
            "means unrecorded, not flat."
        ),
    )
    derived_grade_percent: float | None = Field(
        default=None,
        description=(
            "Gradient estimated from the elevation model, signed along travel: positive climbs. "
            "It describes the ground, not the path, so it cannot see a ramp or a step. Null when "
            "the model gives none — no coverage, a segment too short for it to resolve, or an "
            "implausible value. Routing uses `incline_percent` where present and this otherwise."
        ),
    )
    kerb: KerbType
    is_crossing: bool
    width_m: float | None = Field(
        default=None, description="Recorded width. Null means unrecorded, not narrow."
    )
    unknown_attributes: list[str] = Field(
        description="Routing-relevant attributes nobody has recorded for this segment."
    )
    cost_components: list[CostComponentModel]
    excluded_by_profile: ExclusionReason | None = Field(
        default=None,
        description=(
            "Set on the shortest route only: the chosen profile's hard limit that rules this "
            "segment out. Null where the profile can use it, and always null on the route "
            "computed for the profile."
        ),
    )


class SnappedPointModel(BaseModel):
    """Where a requested coordinate joined the network."""

    model_config = ConfigDict(frozen=True)

    longitude: float
    latitude: float
    distance_m: float = Field(
        description="How far the requested point was from the nearest mapped path."
    )


class GradeExtremeModel(BaseModel):
    """The steepest gradient on a route in one direction."""

    model_config = ConfigDict(frozen=True)

    percent: float = Field(ge=0.0, description="Size of the gradient in percent, without sign.")
    direction: Literal["uphill", "downhill"] = Field(description="In the direction of travel.")
    source: GradeSource = Field(
        description=(
            "'osm_incline' — recorded by a mapper in OpenStreetMap; 'derived_elevation' — "
            "estimated from the elevation model."
        )
    )
    segment_index: int = Field(ge=0, description="Position in this route's `segments`.")


class GradientSummaryModel(BaseModel):
    """The gradients a route was costed on, recorded and estimated kept apart."""

    model_config = ConfigDict(frozen=True)

    steepest_uphill: GradeExtremeModel | None = Field(
        description="Steepest climb along travel, or null when no known gradient climbs."
    )
    steepest_downhill: GradeExtremeModel | None = Field(
        description="Steepest descent along travel, or null when no known gradient descends."
    )
    recorded_fraction: float = Field(
        ge=0.0, le=1.0, description="Share of the route's length with a gradient recorded in OSM."
    )
    estimated_fraction: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Share whose gradient is estimated from the elevation model because OSM records none."
        ),
    )
    unknown_fraction: float = Field(
        ge=0.0,
        le=1.0,
        description="Share with no gradient at all. Unknown, not flat.",
    )


class DurationEstimateModel(BaseModel):
    """How long a route might take: distance over an assumed pace range."""

    model_config = ConfigDict(frozen=True)

    low_seconds: int = Field(ge=0, description="At the fast end of the assumed pace range.")
    high_seconds: int = Field(ge=0, description="At the slow end of the assumed pace range.")
    pace_low_mps: float = Field(gt=0.0, description="Slowest assumed pace, metres per second.")
    pace_high_mps: float = Field(gt=0.0, description="Fastest assumed pace, metres per second.")
    pace_label: str = Field(
        description="Whose pace, in words to show beside the time: 'walking pace'.",
        examples=["walking pace", "wheelchair pace"],
    )
    basis: Literal["assumed_pace"] = Field(
        default="assumed_pace",
        description=(
            "Always 'assumed_pace': distance divided by a planning assumption. No delay is "
            "added for crossings, signals or stairs, and nobody's pace was measured."
        ),
    )


class RouteUnitModel(BaseModel):
    """One road crossing or stairway, as the route passes through it.

    OpenStreetMap draws each as one way, which the network splits into several
    segments; this is the one crossing or stairway those segments make up.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["crossing", "stairway"]
    segment_indexes: list[int] = Field(description="Positions in this route's `segments`.")
    kerb: KerbType | None = Field(
        default=None,
        description=(
            "Crossings only: the crossing's kerb, from every kerb recorded along it. "
            "`unknown` when either end has no record."
        ),
    )
    kerb_recorded_ends: int | None = Field(
        default=None,
        ge=0,
        le=2,
        description="Crossings only: how many ends have a recorded kerb (2 means both).",
    )
    crossing_type: str | None = Field(
        default=None, description="Crossings only: OSM `crossing` value, if recorded."
    )
    step_count: int | None = Field(
        default=None, description="Stairways only: recorded steps, or null when unrecorded."
    )


class RouteModel(BaseModel):
    """A computed route."""

    model_config = ConfigDict(frozen=True)

    profile: str = Field(description="Profile key this route was computed for.")
    profile_display_name: str
    distance_m: float = Field(description="Real ground distance.")
    effective_distance_m: float = Field(
        description="Distance weighted by this profile's cost model. Not a physical distance."
    )
    estimated_duration_seconds: float = Field(
        description=(
            "The middle of `duration_estimate`, for clients that need one figure. A rough "
            "planning figure at the assumed pace of `pace_profile`; prefer the range."
        )
    )
    duration_estimate: DurationEstimateModel = Field(
        description=(
            "Time as a range, at the assumed pace of the profile this route was computed "
            "for. The shortest route is always timed at walking pace; the profile route at "
            "that profile's assumed pace. The two are different paces, so subtracting one "
            "from the other does not say how much time a route saves."
        )
    )
    pace_profile: str = Field(
        description=(
            "Profile whose assumed pace produced the estimate: 'standard' for the shortest "
            "route, the chosen profile — its base preset, for a custom one — for the other."
        )
    )
    coordinates: list[tuple[float, float]] = Field(
        description="[longitude, latitude] polyline for the whole route."
    )
    segments: list[RouteSegmentModel]
    origin: SnappedPointModel
    destination: SnappedPointModel
    stairway_count: int = Field(
        description="Stairways on this route, each counted once however many segments it spans."
    )
    step_count: int = Field(
        description=(
            "Recorded steps across this route's stairways, each stairway counted once. A "
            "stairway with no recorded count adds nothing, so this is a floor."
        )
    )
    crossing_count: int = Field(
        description="Mapped road crossings, each counted once however many segments it spans."
    )
    unknown_kerb_crossing_count: int = Field(
        description="Crossings without a recorded kerb at both ends."
    )
    units: list[RouteUnitModel] = Field(
        default_factory=list,
        description="Every crossing and stairway on this route, in order of travel.",
    )
    steepest_incline_percent: float | None = Field(
        description=(
            "Steepest gradient recorded in OpenStreetMap, either direction, as a magnitude. "
            "Recorded values only — estimates are in `gradient`, which is what to display."
        )
    )
    gradient: GradientSummaryModel = Field(
        description=(
            "Every gradient this route was costed on — recorded where OSM has one, estimated "
            "from the elevation model otherwise — with its source and direction."
        )
    )
    unknown_data_fraction: float = Field(
        ge=0.0,
        le=1.0,
        description="Share of this route's length whose accessibility attributes are unrecorded.",
    )
    evidence_coverage: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Share of this route with no record, per category (surface, smoothness, gradient, "
            "width, kerb). Kerb is measured over crossings only, since it is a fact about "
            "crossings. Reported per category because one combined figure cannot be acted on."
        ),
    )
    gradient_source: str | None = Field(
        default=None,
        description=(
            "Where gradient information came from: 'osm_incline' (recorded by a mapper), "
            "'derived_elevation' (inferred from a terrain model), 'mixed', or null when the "
            "route has no gradient information at all."
        ),
    )
    computation_ms: float


class ExplanationModel(BaseModel):
    """An evidence-backed statement about why the accessible route differs.

    One is produced for every constraint of the chosen profile on which the two
    routes differ: each hard limit the shortest route breaks, and each penalty
    the accessible route incurs less of. No single one is the reason for a
    detour unless it is the only one listed.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    summary: str
    basis: EvidenceBasis = Field(
        description=(
            "What the statement rests on: 'recorded' in OpenStreetMap; 'estimated' from the "
            "elevation model; 'mixed' — recorded and estimated gradients together; "
            "'not_recorded' — the statement is about data nobody has recorded; 'profile_rule' — "
            "a consequence of the profile's rules rather than an observation."
        )
    )
    evidence: dict[str, object] = Field(
        default_factory=dict,
        description="The recorded values this statement was derived from.",
    )
    shortest_route_segments: list[int] = Field(
        default_factory=list,
        description=(
            "Positions in the shortest route's `segments` that this statement is about and "
            "the chosen route does not use: every one, for pointing at on a map."
        ),
    )


class DifferenceSummaryModel(BaseModel):
    """The comparison in one line."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["same_path", "different_path", "no_profile_route", "no_shortest_route"] = Field(
        description=(
            "'same_path' only when both routes travel the same segments and draw the same "
            "line — never inferred from distance."
        )
    )
    headline: str = Field(
        examples=["Same path as shortest route.", "+67 m to avoid 2 recorded stairways."]
    )
    primary_code: str | None = Field(
        default=None,
        description=(
            "The explanation the headline leads with: the first hard limit, otherwise the "
            "penalty that differed most."
        ),
    )
    primary_basis: EvidenceBasis | None = Field(
        default=None,
        description=(
            "What the headline's reason rests on. 'not_recorded' means the route avoids "
            "missing data, not a recorded barrier."
        ),
    )


class CautionModel(BaseModel):
    """Something to weigh before relying on the route."""

    model_config = ConfigDict(frozen=True)

    code: str
    summary: str
    evidence: dict[str, object] = Field(default_factory=dict)


class DatasetProvenance(BaseModel):
    """Which network answered this request."""

    model_config = ConfigDict(frozen=True)

    dataset_id: str
    region: str
    checksum: str = Field(description="Content hash of the dataset that produced this route.")
    source_type: Literal["osm", "synthetic"]
    source_name: str
    acquired_at: str = Field(
        description="When PathAble obtained this dataset, ISO 8601. Not how old the map is."
    )
    source_timestamp: str | None = Field(
        default=None,
        description=(
            "When the upstream source published this data, ISO 8601. This is the figure that "
            "says how current the map is; `acquired_at` only says when it was fetched. Null "
            "when the source published no timestamp."
        ),
    )
    evidence_age_days: int | None = Field(
        default=None,
        description=(
            "Whole days between the upstream publication and this response. Null when the "
            "source published no timestamp. A route cannot say a particular crossing was "
            "surveyed years ago — OpenStreetMap element timestamps are not yet ingested — so "
            "this is the age of the dataset, not of any individual fact in it."
        ),
    )
    attribution: str = Field(
        description="Required credit for the underlying map data.",
        examples=["© OpenStreetMap contributors, ODbL 1.0"],
    )
    elevation_attribution: str | None = Field(
        default=None,
        description=(
            "Required credit for the elevation model behind any derived gradient in this "
            "response. Null when the dataset carries no elevation at all, in which case every "
            "gradient shown was recorded by a mapper or is unknown."
        ),
        examples=[
            # The licence's own wording uses an en dash.
            "Contains information licensed under the Open Government Licence – Canada."  # noqa: RUF001
        ],
    )


class RouteCompareResponse(BaseModel):
    """The shortest route, the accessible route, and the difference between them."""

    model_config = ConfigDict(frozen=True)

    profile: str
    profile_display_name: str
    profile_description: str
    standard_route: RouteModel | None = Field(
        default=None, description="Shortest walking route. Null when none exists."
    )
    accessible_route: RouteModel | None = Field(
        default=None,
        description="Route meeting the chosen profile. Null when no such route exists.",
    )
    standard_failure: str | None = Field(
        default=None, description="Why the shortest route could not be computed."
    )
    accessible_failure: str | None = Field(
        default=None, description="Why no route satisfies the chosen profile."
    )
    extra_distance_m: float | None = Field(
        default=None, description="How much further the accessible route is."
    )
    extra_distance_fraction: float | None = None
    summary: DifferenceSummaryModel = Field(
        description="The comparison in one line, for reading before anything else."
    )
    explanations: list[ExplanationModel]
    cautions: list[CautionModel]
    dataset: DatasetProvenance
    routing_policy_version: int = Field(
        description=(
            "Which cost and constraint policy produced this route. Bumped whenever a "
            "change would move a route, so two results can be compared meaningfully."
        )
    )
    ml_predictions_used: Literal[False] = Field(
        default=False,
        description=(
            "Always false. PathAble's routing is deterministic rules over recorded map "
            "attributes; no model prediction contributes to any cost or constraint."
        ),
    )


class MobilityProfileModel(BaseModel):
    """A profile a client can offer."""

    model_config = ConfigDict(frozen=True)

    key: str
    display_name: str
    description: str
    excludes_steps: bool = Field(
        description="Whether stairways are excluded outright for this profile."
    )
    max_incline_percent: float | None = Field(
        default=None,
        description=(
            "A hard limit: uphill gradients above this, recorded or estimated, are excluded. "
            "Null for every preset — a preset expresses gradient as preference, not "
            "impossibility."
        ),
    )
    min_width_m: float | None = Field(
        default=None, description="A hard limit: recorded widths below this are excluded."
    )
    prefers_gradient_under_percent: float | None = Field(
        default=None,
        description=(
            "Guidance, not a limit. Steeper segments cost far more but remain available "
            "when the only alternative is no route."
        ),
    )
    prefers_width_over_m: float | None = Field(default=None, description="Guidance, not a limit.")
    hard_requirements: list[str] = Field(
        default_factory=list,
        description="Plain statements of what this traveller cannot use. Empty when nothing is excluded.",
    )


class MobilityProfileListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    profiles: list[MobilityProfileModel]
