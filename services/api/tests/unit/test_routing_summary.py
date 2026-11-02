"""The comparison in one line, and time as an honest range."""

from __future__ import annotations

import pytest

from pathable_api.routing.comparison import compare_routes, routes_share_path
from pathable_api.routing.engine import DisconnectedEndpointsError, compute_route
from pathable_api.routing.profiles import (
    SELECTABLE_PROFILE_KEYS,
    STANDARD,
    WALKING_PACE,
    get_profile,
)
from tests.unit.test_routing_units import Network, at, journey, stair_network

WHEELCHAIR = get_profile("wheelchair")


def straight_path() -> tuple[Network, dict[str, str]]:
    n = Network()
    west, middle, east = n.node("w", 0, 0), n.node("m", 60, 0), n.node("e", 120, 0)
    n.way("path", [west, middle, east], {"highway": "footway", "surface": "asphalt"})
    return n, {"west": west, "east": east}


class TestSummary:
    def test_identical_routes_say_so_and_claim_nothing_else(self) -> None:
        n, ends = straight_path()
        origin, destination = journey(n, ends)
        comparison = compare_routes(
            n.graph(), origin=origin, destination=destination, profile=WHEELCHAIR
        )

        assert comparison.same_path
        assert comparison.summary.kind == "same_path"
        assert comparison.summary.headline == "Same path as shortest route."
        assert comparison.summary.primary_code is None
        assert [item.code for item in comparison.explanations] == ["same_distance"]

    def test_a_detour_says_how_far_and_why(self) -> None:
        n, ends = stair_network(step_count=12, pieces=2)
        origin, destination = journey(n, ends)
        comparison = compare_routes(
            n.graph(), origin=origin, destination=destination, profile=WHEELCHAIR
        )
        standard, accessible = comparison.standard_route, comparison.accessible_route
        assert standard is not None
        assert accessible is not None

        extra = round(accessible.distance_m - standard.distance_m)
        assert comparison.summary.kind == "different_path"
        assert comparison.summary.headline == f"+{extra} m to avoid 1 recorded stairway."
        assert comparison.summary.primary_code == "avoids_stairs"
        assert comparison.summary.primary_basis == "recorded"

    def test_an_explanation_points_at_every_segment_it_is_about(self) -> None:
        n, ends = stair_network(step_count=None, pieces=3)
        origin, destination = journey(n, ends)
        comparison = compare_routes(
            n.graph(), origin=origin, destination=destination, profile=WHEELCHAIR
        )
        standard = comparison.standard_route
        assert standard is not None

        stairs = next(item for item in comparison.explanations if item.code == "avoids_stairs")
        on_stairs = tuple(
            index for index, segment in enumerate(standard.segments) if segment.features.unit_ref
        )
        assert stairs.shortest_route_segments == on_stairs
        assert len(on_stairs) == 3

    def test_no_route_for_the_profile_is_its_own_answer(self) -> None:
        n = Network()
        bottom, top = n.node("b", 0, 0), n.node("t", 20, 0)
        n.way("stairs", [bottom, top], {"highway": "steps"})
        comparison = compare_routes(
            n.graph(),
            origin=journey(n, {"west": bottom, "east": top})[0],
            destination=journey(n, {"west": bottom, "east": top})[1],
            profile=WHEELCHAIR,
        )

        assert comparison.accessible_route is None
        assert comparison.summary.kind == "no_profile_route"

    def test_same_path_is_never_read_from_distance(self) -> None:
        n, ends = stair_network(step_count=None, pieces=1)
        origin, destination = journey(n, ends)
        graph = n.graph()
        standard = compute_route(graph, origin=origin, destination=destination, profile=STANDARD)
        accessible = compute_route(
            graph, origin=origin, destination=destination, profile=WHEELCHAIR
        )

        assert routes_share_path(standard, standard)
        assert not routes_share_path(standard, accessible)
        assert not routes_share_path(standard, None)


class TestDuration:
    def test_it_is_distance_over_a_pace_range_and_nothing_else(self) -> None:
        # Regression: 20 s per crossing and 2.5 s per step were added for
        # everybody, which made a guess read like a calculation.
        n, ends = stair_network(step_count=40, pieces=2)
        origin, destination = journey(n, ends)
        route = compute_route(n.graph(), origin=origin, destination=destination, profile=STANDARD)

        assert route.stairway_count == 1
        assert route.duration.low_seconds == pytest.approx(route.distance_m / WALKING_PACE.high_mps)
        assert route.duration.high_seconds == pytest.approx(route.distance_m / WALKING_PACE.low_mps)
        assert route.duration.pace_profile_key == "standard"

    def test_every_profile_route_is_timed_at_its_own_pace(self) -> None:
        n, ends = straight_path()
        origin, destination = journey(n, ends)
        graph = n.graph()
        for key in SELECTABLE_PROFILE_KEYS:
            profile = get_profile(key)
            comparison = compare_routes(
                graph, origin=origin, destination=destination, profile=profile
            )
            accessible = comparison.accessible_route
            standard = comparison.standard_route
            assert accessible is not None
            assert standard is not None
            assert accessible.duration.pace == profile.pace
            assert standard.duration.pace == WALKING_PACE
            assert accessible.duration.low_seconds <= accessible.duration.high_seconds

    def test_every_assumed_pace_is_a_real_range(self) -> None:
        for profile in (STANDARD, *(get_profile(key) for key in SELECTABLE_PROFILE_KEYS)):
            assert 0 < profile.pace.low_mps < profile.pace.high_mps
            assert profile.pace.described_as


class TestDisconnectedParts:
    def test_points_on_parts_that_never_meet_fail_without_a_search(self) -> None:
        # Regression: a RIM Park point snapped onto a 1,887-node part of the
        # Waterloo network that no mapped path joins to the rest. The search
        # explored everything reachable before reporting "no route satisfies the
        # standard walking profile", which was slow and misdescribed the cause.
        n = Network()
        a, b = n.node("a", 0, 0), n.node("b", 100, 0)
        c, d = n.node("c", 0, 120), n.node("d", 100, 120)
        n.way("south", [a, b], {"highway": "footway"})
        n.way("north", [c, d], {"highway": "footway"})
        graph = n.graph()

        with pytest.raises(DisconnectedEndpointsError, match="do not connect"):
            compute_route(
                graph,
                origin=journey(n, {"west": a, "east": b})[0],
                destination=journey(n, {"west": c, "east": d})[1],
                profile=STANDARD,
            )
        comparison = compare_routes(
            graph,
            origin=journey(n, {"west": a, "east": b})[0],
            destination=journey(n, {"west": c, "east": d})[1],
            profile=WHEELCHAIR,
        )
        assert comparison.standard_route is None
        assert comparison.standard_failure is not None
        assert "do not connect" in comparison.standard_failure


class TestSnappingToConnectedPaths:
    def test_a_corridor_behind_a_private_door_is_not_where_a_route_starts(self) -> None:
        # Regression: a Columbia Icefield point snapped onto an indoor corridor
        # joined to the campus only through access=private segments. Counted as
        # connected, the corridor gave "no route" for every profile.
        n = Network()
        inside, door = n.node("i", 0, 0), n.node("d", 0, 12)
        out_west, out_east = n.node("ow", -40, 30), n.node("oe", 140, 30)
        n.way("corridor", [inside, n.node("c", 20, 0)], {"highway": "corridor"})
        n.way("door", [inside, door], {"highway": "footway", "access": "private"})
        n.way("street", [out_west, n.node("m", 60, 30), out_east], {"highway": "footway"})
        n.way("link", [door, n.node("x", 0, 30)], {"highway": "footway", "access": "private"})
        graph = n.graph()

        route = compute_route(
            graph, origin=at(5, 2), destination=at(*n.nodes[out_east]), profile=STANDARD
        )
        assert route.origin.distance_m > 20  # moved to the street, and says how far
        assert all(not seg.features.general_access.is_prohibited for seg in route.segments)

    def test_a_step_free_route_starts_on_a_step_free_path_nearby(self) -> None:
        # Regression: the Dana Porter Library's nearest path is reachable only
        # by a stairway in the map data, so every step-free profile got "no
        # route" with a step-free path 25 m away.
        n = Network()
        terrace, foot = n.node("t", 0, 0), n.node("f", 0, -10)
        west, east = n.node("w", -50, -30), n.node("e", 150, -30)
        street_middle = n.node("s", 0, -30)
        n.way("terrace", [terrace, n.node("t2", 15, 0)], {"highway": "footway"})
        n.way("stairs", [terrace, foot], {"highway": "steps"})
        n.way("street", [west, street_middle, east], {"highway": "footway"})
        n.way("down", [foot, street_middle], {"highway": "footway"})
        graph = n.graph()

        comparison = compare_routes(
            graph, origin=at(5, 1), destination=at(*n.nodes[east]), profile=WHEELCHAIR
        )
        assert comparison.standard_route is not None
        assert comparison.accessible_route is not None
        assert comparison.accessible_route.stairway_count == 0
        moved = [c for c in comparison.cautions if c.code == "profile_route_moved_endpoint"]
        assert moved
        assert "starts" in moved[0].summary
