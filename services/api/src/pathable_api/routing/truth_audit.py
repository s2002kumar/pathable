"""The product truth audit: real journeys, every profile, every claim checked.

A route is only as good as what is said about it. For every journey in
`routing.truth_corpus` and every mobility profile, this computes the comparison
a person would see and checks it against itself:

* **Defects** are contradictions that must never happen: a shortest route that
  moves when the profile changes; a hard requirement broken to return a route;
  a polyline with gaps; a distance that is not the sum of its parts; a summary
  that says "same path" about two paths; an explanation pointing at a segment
  the chosen route actually uses; a time that is not distance over the stated
  pace; a sentence with "inf" or "nan" where a number should be.
* **Flags** are outcomes worth a person's eyes, not errors: a long detour taken
  to avoid missing data rather than a recorded barrier; a profile route
  physically shorter than the shortest one; a snap far from the requested point.
  A flag is inspected and explained, never silently "fixed".

Nothing is filtered by outcome. Every case and profile is reported.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any, Literal

from shapely.geometry import Point

from pathable_api.geo.enums import SurfaceClass, TriState
from pathable_api.geo.geometry import geodesic_distance_m
from pathable_api.routing.comparison import RouteComparison, compare_routes, routes_share_path
from pathable_api.routing.engine import PointOffNetworkError, Route, RoutingError
from pathable_api.routing.graph import RoutableGraph
from pathable_api.routing.profiles import (
    SELECTABLE_PROFILE_KEYS,
    MobilityProfile,
    build_custom_profile,
    get_profile,
)
from pathable_api.routing.truth_corpus import AuditCase

#: Bumped whenever a check is added, removed or changes what it accepts.
TRUTH_AUDIT_VERSION = 1

Severity = Literal["defect", "flag"]

#: Two segments "meet" when the end of one is this close to the start of the next.
_SEAM_TOLERANCE_M = 1.0
#: Snapping further than this from the requested point is worth a look.
_FAR_SNAP_M = 50.0
#: The two routes of one comparison should start from the same place unless the
#: profile could not use the shortest route's first segment.
_SNAP_MISMATCH_M = 5.0
#: A detour this long, taken mainly to avoid missing data, is worth a look.
_WEAK_DETOUR_M = 50.0
_WEAK_DETOUR_FRACTION = 0.20
#: A detour this long, whatever its reason, is worth a look.
_LARGE_DETOUR_M = 300.0
_LARGE_DETOUR_FRACTION = 0.50


def audit_profiles() -> tuple[MobilityProfile, ...]:
    """Every selectable profile, and one custom profile with a declared gradient limit.

    The custom one is there because only a declared limit is a hard constraint
    on gradient, and a hard constraint is what the audit most needs to see held.
    """
    return (
        *(get_profile(key) for key in SELECTABLE_PROFILE_KEYS),
        build_custom_profile(base="wheelchair", max_incline_percent=5.0),
    )


@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    severity: Severity
    profile: str | None
    detail: str


@dataclass(slots=True)
class ProfileOutcome:
    """One case, one profile: what was shown and what was wrong with it."""

    profile: str
    outcome: Literal["compared", "no_profile_route", "no_route"]
    summary_kind: str | None = None
    headline: str | None = None
    primary_code: str | None = None
    primary_basis: str | None = None
    explanation_codes: list[str] = field(default_factory=list)
    route_m: float | None = None
    extra_m: float | None = None
    same_path: bool | None = None
    route_stairways: int | None = None
    route_crossings: int | None = None
    route_unknown_kerb_crossings: int | None = None
    route_duration_s: tuple[int, int] | None = None
    route_snap_m: tuple[float, float] | None = None
    failure: str | None = None


@dataclass(slots=True)
class CaseOutcome:
    case: AuditCase
    standard_fingerprint: str | None = None
    standard_m: float | None = None
    standard_stairways: int | None = None
    standard_crossings: int | None = None
    standard_duration_s: tuple[int, int] | None = None
    standard_snap_m: tuple[float, float] | None = None
    standard_failure: str | None = None
    profiles: list[ProfileOutcome] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


#: Turns a comparison into the response a client receives, so the audit can
#: check what is actually sent and not only the objects behind it.
Serializer = Callable[[RouteComparison], dict[str, Any]]


def fingerprint(route: Route) -> str:
    """Everything that makes the shortest route the shortest route, hashed."""
    payload = {
        "segments": [segment.edge_identity for segment in route.segments],
        "coordinates": [[round(x, 9), round(y, 9)] for x, y in route.coordinates],
        "distance_m": round(route.distance_m, 3),
        "duration_s": [
            round(route.duration.low_seconds, 3),
            round(route.duration.high_seconds, 3),
        ],
    }
    return hashlib.sha256(json.dumps(payload).encode("utf-8")).hexdigest()


def _metres_between(first: tuple[float, float], second: tuple[float, float]) -> float:
    return geodesic_distance_m(Point(*first), Point(*second))


def _polyline_length(coordinates: Sequence[tuple[float, float]]) -> float:
    return sum(_metres_between(a, b) for a, b in pairwise(coordinates))


def check_route(route: Route, profile: MobilityProfile, label: str) -> list[Finding]:
    """What must be true of any route, whoever it was computed for."""
    found: list[Finding] = []

    def defect(check: str, detail: str) -> None:
        found.append(Finding(check, "defect", profile.key, f"{label}: {detail}"))

    seams = [
        _metres_between(first.coordinates[-1], second.coordinates[0])
        for first, second in pairwise(route.segments)
    ]
    if seams and max(seams) > _SEAM_TOLERANCE_M:
        defect("continuity", f"segments are {max(seams):.1f} m apart at one join")
    if route.coordinates:
        origin = (route.origin.longitude, route.origin.latitude)
        destination = (route.destination.longitude, route.destination.latitude)
        start = _metres_between(route.coordinates[0], origin)
        end = _metres_between(route.coordinates[-1], destination)
        if max(start, end) > _SEAM_TOLERANCE_M:
            defect("continuity", f"the line ends {max(start, end):.1f} m from its snapped point")

    summed = sum(segment.length_m for segment in route.segments)
    if abs(summed - route.distance_m) > 0.01:
        defect("distance", f"distance {route.distance_m:.2f} m but segments sum to {summed:.2f}")
    drawn = _polyline_length(route.coordinates)
    if abs(drawn - route.distance_m) > max(1.0, 0.01 * route.distance_m):
        defect("distance", f"distance {route.distance_m:.1f} m but the line is {drawn:.1f} m")

    for index, segment in enumerate(route.segments):
        features = segment.features
        if features.foot_access.is_prohibited or features.general_access.is_prohibited:
            defect("hard_constraint", f"segment {index} prohibits pedestrians")

    pace = route.duration.pace
    expected = (route.distance_m / pace.high_mps, route.distance_m / pace.low_mps)
    actual = (route.duration.low_seconds, route.duration.high_seconds)
    if any(abs(a - b) > 0.01 for a, b in zip(actual, expected, strict=True)):
        defect("eta", f"time {actual} is not distance over the {pace.described_as} {expected}")
    return found


def check_hard_limits(route: Route, profile: MobilityProfile) -> list[Finding]:
    """A hard requirement the profile declared is never broken to return a route."""
    limits = profile.hard_limits
    found: list[Finding] = []
    for index, segment in enumerate(route.segments):
        features = segment.features
        problems = []
        if limits.exclude_steps and features.steps is TriState.YES:
            problems.append("a stairway")
        grade = features.effective_grade_percent
        limit = limits.max_incline_percent
        if limit is not None and grade is not None and grade > limit:
            problems.append(f"a {grade:.1f}% climb")
        width = limits.min_width_m
        if width is not None and features.width_m is not None and features.width_m < width:
            problems.append(f"{features.width_m} m wide")
        if limits.exclude_rough_surface and features.surface_class is SurfaceClass.ROUGH:
            problems.append("a rough surface")
        found.extend(
            Finding("hard_constraint", "defect", profile.key, f"segment {index} is {problem}")
            for problem in problems
        )
    return found


def check_comparison(comparison: RouteComparison) -> list[Finding]:
    """What is said about the two routes must match the two routes."""
    profile = comparison.profile
    standard, accessible = comparison.standard_route, comparison.accessible_route
    summary = comparison.summary
    found: list[Finding] = []

    def defect(check: str, detail: str) -> None:
        found.append(Finding(check, "defect", profile.key, detail))

    def flag(check: str, detail: str) -> None:
        found.append(Finding(check, "flag", profile.key, detail))

    if standard is None or accessible is None:
        if accessible is None and summary.kind != "no_profile_route":
            defect("explanation", f"no profile route but the summary is {summary.kind}")
        return found

    same = routes_share_path(standard, accessible)
    extra = accessible.distance_m - standard.distance_m
    moved = _ends_moved(standard, accessible)
    # Two routes that end in different places are different journeys: no
    # figure, explanation or flag may set their lengths against each other.
    if moved:
        if comparison.distance_comparable or comparison.extra_distance_m is not None:
            defect("distance", f"the ends moved but a difference of {extra:+.1f} m is reported")
        compared = [
            item.code
            for item in comparison.explanations
            if item.code in ("distance_difference", "same_distance")
            or "extra_distance_m" in item.evidence
        ]
        if compared:
            defect("explanation", f"the ends moved but {compared} compare the lengths")
    elif comparison.extra_distance_m is None or abs(comparison.extra_distance_m - extra) > 0.01:
        defect("distance", f"{extra:+.1f} m apart but {comparison.extra_distance_m} is reported")
    if same != (summary.kind == "same_path"):
        defect("explanation", f"same path is {same} but the summary is {summary.kind}")
    if same:
        claims = [item.code for item in comparison.explanations if item.code != "same_distance"]
        if claims:
            defect("explanation", f"the same path, yet it claims {claims}")
    elif moved:
        # Routes that start or end at different points are not compared by
        # length; the headline must say where the profile route goes instead.
        if "m away" not in summary.headline:
            defect("explanation", f"the ends moved but the headline reads {summary.headline!r}")
    else:
        headline = summary.headline
        if round(extra) > 0 and not headline.startswith(f"+{round(extra):,} m"):
            defect("explanation", f"{extra:.1f} m longer but the headline reads {headline!r}")
        if round(extra) < 0 and "shorter" not in headline:
            defect("explanation", f"{-extra:.1f} m shorter but the headline reads {headline!r}")
        if summary.primary_code is None:
            flag(
                "unexplained_difference", f"a different path with no stated reason ({extra:+.1f} m)"
            )

    used = {segment.edge_identity for segment in accessible.segments}
    for item in comparison.explanations:
        for index in item.shortest_route_segments:
            if not 0 <= index < len(standard.segments):
                defect("evidence", f"{item.code} points at segment {index}, not on the route")
            elif standard.segments[index].edge_identity in used:
                defect("evidence", f"{item.code} points at a segment the chosen route uses")
        unrecorded = item.code.startswith("avoids_unrecorded")
        if unrecorded and item.basis != "not_recorded":
            defect("evidence", f"{item.code} is about missing data but its basis is {item.basis}")
        if item.evidence.get("hard_limit") is True and item.basis == "not_recorded":
            defect("evidence", f"{item.code} is a hard limit resting on missing data")
    primary = next(
        (item for item in comparison.explanations if item.code == summary.primary_code), None
    )
    if primary is not None and primary.basis != summary.primary_basis:
        defect("evidence", "the headline's basis is not its explanation's")

    if standard.pace_profile_key != "standard":
        defect("eta", f"the shortest route is timed at {standard.pace_profile_key} pace")
    if accessible.pace_profile_key != profile.pace_profile_key:
        defect("eta", f"the profile route is timed at {accessible.pace_profile_key} pace")

    if profile.hard_limits.exclude_steps and accessible.stairway_count:
        defect("hard_constraint", f"{accessible.stairway_count} stairways on a step-free route")

    if not moved:
        if extra < -1.0:
            flag("profile_route_shorter", f"the profile route is {-extra:.1f} m shorter")
        threshold = max(_WEAK_DETOUR_M, _WEAK_DETOUR_FRACTION * standard.distance_m)
        if not same and summary.primary_basis == "not_recorded" and extra >= threshold:
            flag(
                "weak_evidence_detour",
                f"+{extra:.0f} m ({extra / standard.distance_m:.0%}) led by {summary.primary_code}",
            )
        if extra >= max(_LARGE_DETOUR_M, _LARGE_DETOUR_FRACTION * standard.distance_m):
            flag("large_detour", f"+{extra:.0f} m ({extra / standard.distance_m:.0%})")
    for end, mine, theirs in (
        ("origin", accessible.origin, standard.origin),
        ("destination", accessible.destination, standard.destination),
    ):
        apart = _metres_between(
            (mine.longitude, mine.latitude), (theirs.longitude, theirs.latitude)
        )
        if apart > _SNAP_MISMATCH_M:
            flag("snap_mismatch", f"the {end} snapped {apart:.1f} m from the shortest route's")
    return found


def _ends_moved(standard: Route, accessible: Route) -> bool:
    return any(
        _metres_between((ours.longitude, ours.latitude), (theirs.longitude, theirs.latitude))
        > _SNAP_MISMATCH_M
        for ours, theirs in (
            (accessible.origin, standard.origin),
            (accessible.destination, standard.destination),
        )
    )


def check_response(response: dict[str, Any], comparison: RouteComparison) -> list[Finding]:
    """What a client receives must say what the comparison says."""
    profile = comparison.profile.key
    found: list[Finding] = []

    def defect(detail: str) -> None:
        found.append(Finding("response", "defect", profile, detail))

    summary = response["summary"]
    if summary["headline"] != comparison.summary.headline:
        defect("the headline sent differs from the one computed")
    if response["distance_comparable"] != comparison.distance_comparable:
        defect("distance_comparable sent differs from the one computed")
    if not response["distance_comparable"] and response["extra_distance_m"] is not None:
        defect("a distance difference is sent for routes that are not compared")
    for name, route in (
        ("standard_route", comparison.standard_route),
        ("accessible_route", comparison.accessible_route),
    ):
        sent = response.get(name)
        if route is None or sent is None:
            if (route is None) != (sent is None):
                defect(f"{name} is present on one side only")
            continue
        if [tuple(position) for position in sent["coordinates"]] != list(route.coordinates):
            defect(f"{name}'s drawn line differs from the route")
        kinds = Counter(unit["kind"] for unit in sent["units"])
        if sent["crossing_count"] != kinds["crossing"]:
            defect(
                f"{name} counts {sent['crossing_count']} crossings and lists {kinds['crossing']}"
            )
        if sent["stairway_count"] != kinds["stairway"]:
            defect(
                f"{name} counts {sent['stairway_count']} stairways and lists {kinds['stairway']}"
            )
        duration = sent["duration_estimate"]
        if not duration["low_seconds"] <= duration["high_seconds"]:
            defect(f"{name}'s time range runs backwards")
    return found


def _rounded_snap(route: Route) -> tuple[float, float]:
    return (round(route.origin.distance_m, 1), round(route.destination.distance_m, 1))


def _seconds(route: Route) -> tuple[int, int]:
    return (round(route.duration.low_seconds), round(route.duration.high_seconds))


#: A number a person cannot read: ``inf`` or ``nan`` printed into a sentence.
_NOT_A_NUMBER = re.compile(r"\b(?:inf|nan)\b", re.IGNORECASE)


def check_wording(text: str | None, profile: str | None) -> list[Finding]:
    """What people read never carries a number that is not one.

    Found on real data: open farmland answered "The origin is inf m from the
    nearest mapped path".
    """
    if text is None or not _NOT_A_NUMBER.search(text):
        return []
    return [Finding("wording", "defect", profile, f"unreadable number in {text!r}")]


def audit_case(
    graph: RoutableGraph,
    case: AuditCase,
    profiles: Sequence[MobilityProfile],
    serialize: Serializer | None = None,
) -> CaseOutcome:
    """Every check, for one journey and every profile."""
    outcome = CaseOutcome(case=case)
    fingerprints: dict[str, str] = {}

    for profile in profiles:
        try:
            comparison = compare_routes(
                graph, origin=case.origin, destination=case.destination, profile=profile
            )
        except PointOffNetworkError as error:  # pragma: no cover - compare_routes catches it
            outcome.profiles.append(
                ProfileOutcome(profile=profile.key, outcome="no_route", failure=str(error))
            )
            continue
        except RoutingError as error:  # pragma: no cover - compare_routes catches it
            outcome.profiles.append(
                ProfileOutcome(profile=profile.key, outcome="no_route", failure=str(error))
            )
            continue

        standard, accessible = comparison.standard_route, comparison.accessible_route
        if standard is not None:
            fingerprints[profile.key] = fingerprint(standard)
            if outcome.standard_fingerprint is None:
                outcome.standard_fingerprint = fingerprints[profile.key]
                outcome.standard_m = round(standard.distance_m, 1)
                outcome.standard_stairways = standard.stairway_count
                outcome.standard_crossings = standard.crossing_count
                outcome.standard_duration_s = _seconds(standard)
                outcome.standard_snap_m = _rounded_snap(standard)
                outcome.findings.extend(check_route(standard, profile, "shortest route"))
        else:
            outcome.standard_failure = comparison.standard_failure

        if standard is None and accessible is None:
            outcome.profiles.append(
                ProfileOutcome(
                    profile=profile.key,
                    outcome="no_route",
                    failure=comparison.standard_failure or comparison.accessible_failure,
                )
            )
            continue

        result = ProfileOutcome(
            profile=profile.key,
            outcome="compared" if accessible is not None else "no_profile_route",
            summary_kind=comparison.summary.kind,
            headline=comparison.summary.headline,
            primary_code=comparison.summary.primary_code,
            primary_basis=comparison.summary.primary_basis,
            explanation_codes=[item.code for item in comparison.explanations],
            failure=comparison.accessible_failure,
        )
        if accessible is not None:
            result.route_m = round(accessible.distance_m, 1)
            result.route_stairways = accessible.stairway_count
            result.route_crossings = accessible.crossing_count
            result.route_unknown_kerb_crossings = accessible.unknown_kerb_crossing_count
            result.route_duration_s = _seconds(accessible)
            result.route_snap_m = _rounded_snap(accessible)
            outcome.findings.extend(check_route(accessible, profile, "profile route"))
            outcome.findings.extend(check_hard_limits(accessible, profile))
        if standard is not None and accessible is not None:
            result.extra_m = round(accessible.distance_m - standard.distance_m, 1)
            result.same_path = comparison.same_path
        for route in (standard, accessible):
            if (
                route is not None
                and max(route.origin.distance_m, route.destination.distance_m) > _FAR_SNAP_M
            ):
                outcome.findings.append(
                    Finding(
                        "far_snap",
                        "flag",
                        profile.key,
                        f"snapped {max(route.origin.distance_m, route.destination.distance_m):.0f} m "
                        "from a requested point",
                    )
                )
                break
        outcome.findings.extend(check_comparison(comparison))
        if serialize is not None:
            outcome.findings.extend(check_response(serialize(comparison), comparison))
        outcome.profiles.append(result)

    for result in outcome.profiles:
        for text in (result.headline, result.failure):
            outcome.findings.extend(check_wording(text, result.profile))

    if len(set(fingerprints.values())) > 1:
        outcome.findings.append(
            Finding(
                "standard_invariance",
                "defect",
                None,
                f"the shortest route differs between profiles: {sorted(set(fingerprints.values()))}",
            )
        )
    return outcome


def run_audit(
    graph: RoutableGraph,
    corpus: Sequence[AuditCase],
    profiles: Sequence[MobilityProfile] | None = None,
    serialize: Serializer | None = None,
    progress: Callable[[CaseOutcome], None] | None = None,
) -> list[CaseOutcome]:
    chosen = tuple(profiles) if profiles is not None else audit_profiles()
    outcomes: list[CaseOutcome] = []
    for case in corpus:
        outcome = audit_case(graph, case, chosen, serialize)
        outcomes.append(outcome)
        if progress is not None:
            progress(outcome)
    return outcomes


def totals(outcomes: Sequence[CaseOutcome]) -> dict[str, Any]:
    """Counts a reader can check against the per-case records."""
    findings = [finding for outcome in outcomes for finding in outcome.findings]
    comparisons = [result for outcome in outcomes for result in outcome.profiles]
    return {
        "cases": len(outcomes),
        "comparisons": len(comparisons),
        "outcomes": dict(sorted(Counter(result.outcome for result in comparisons).items())),
        "same_path": sum(1 for result in comparisons if result.same_path is True),
        "different_path": sum(1 for result in comparisons if result.same_path is False),
        "defects": dict(
            sorted(Counter(f.check for f in findings if f.severity == "defect").items())
        ),
        "flags": dict(sorted(Counter(f.check for f in findings if f.severity == "flag").items())),
        "standard_invariant_cases": sum(
            1
            for outcome in outcomes
            if not any(f.check == "standard_invariance" for f in outcome.findings)
        ),
    }


def as_records(outcomes: Sequence[CaseOutcome]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for outcome in outcomes:
        case = outcome.case
        records.append(
            {
                # "case", as waterloo-routes.json names a journey. Named "key",
                # the secret scan read "gradient-40585154-up" as a credential.
                "case": case.key,
                "description": case.description,
                "categories": list(case.categories),
                "origin": list(case.origin),
                "destination": list(case.destination),
                "standard": {
                    "fingerprint": outcome.standard_fingerprint,
                    "distance_m": outcome.standard_m,
                    "stairways": outcome.standard_stairways,
                    "crossings": outcome.standard_crossings,
                    "duration_s": outcome.standard_duration_s,
                    "snap_m": outcome.standard_snap_m,
                    "failure": outcome.standard_failure,
                },
                "profiles": [
                    {
                        "profile": result.profile,
                        "outcome": result.outcome,
                        "summary_kind": result.summary_kind,
                        "headline": result.headline,
                        "primary_code": result.primary_code,
                        "primary_basis": result.primary_basis,
                        "explanations": result.explanation_codes,
                        "distance_m": result.route_m,
                        "extra_m": result.extra_m,
                        "same_path": result.same_path,
                        "stairways": result.route_stairways,
                        "crossings": result.route_crossings,
                        "unknown_kerb_crossings": result.route_unknown_kerb_crossings,
                        "duration_s": result.route_duration_s,
                        "snap_m": result.route_snap_m,
                        "failure": result.failure,
                    }
                    for result in outcome.profiles
                ],
                "findings": [
                    {
                        "check": finding.check,
                        "severity": finding.severity,
                        "profile": finding.profile,
                        "detail": finding.detail,
                    }
                    for finding in outcome.findings
                ],
            }
        )
    return records
