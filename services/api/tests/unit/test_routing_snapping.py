"""Where a request joins the network, and how a route leaves that point.

An acceptance finding on Waterloo Core v2, reproduced here before it was
fixed: the widening search for a usable path stopped at the first window holding
any segment at all, so closed paths within 60 m hid an open one 60 to 150 m away.
"""

from __future__ import annotations

import pytest

from pathable_api.routing.comparison import compare_routes
from pathable_api.routing.engine import compute_route
from pathable_api.routing.profiles import STANDARD, get_profile
from tests.unit.test_routing_units import Network, at

WHEELCHAIR = get_profile("wheelchair")

FOOTWAY = {"highway": "footway"}
CLOSED_CYCLEWAY = {"highway": "cycleway", "foot": "no"}


def _walkable(edge: object) -> bool:
    features = edge.features  # type: ignore[attr-defined]
    return not (features.foot_access.is_prohibited or features.general_access.is_prohibited)


class TestTheSearchReachesPastUnusablePaths:
    def test_closed_paths_within_60_m_do_not_hide_an_open_one_at_100_m(self) -> None:
        # Regression: the radius sweep returned the first window that held any
        # segment. A closed cycleway 20 m away filled the 60 m window, every
        # candidate was refused, and the open footway 100 m away — inside the
        # 150 m snapping limit — was never looked at: "off the network".
        n = Network()
        n.way("cycleway", [n.node("c1", -50, 20), n.node("c2", 50, 20)], CLOSED_CYCLEWAY)
        n.way("footway", [n.node("f1", -50, -100), n.node("f2", 150, -100)], FOOTWAY)
        graph = n.graph()

        snap = graph.snap_to_edge(*at(0, 0), accept=_walkable, max_distance_m=150.0)
        assert snap is not None
        assert snap.edge.features.highway == "footway"
        assert snap.distance_m == pytest.approx(100.0, abs=1.0)

        route = compute_route(graph, origin=at(0, 0), destination=at(140, -100), profile=STANDARD)
        assert route.origin.distance_m == pytest.approx(100.0, abs=1.0)

    def test_the_nearest_usable_path_wins_not_the_first_one_found(self) -> None:
        # A degree of longitude is shorter than a degree of latitude here, so
        # a window measured in degrees reaches further north than east. An
        # open path 70 m north fell inside the first window and one 62 m east
        # did not; taking the window's best gave the farther of the two.
        n = Network()
        n.way("cycleway", [n.node("c1", -5, 10), n.node("c2", 5, 10)], CLOSED_CYCLEWAY)
        n.way("north", [n.node("n1", -20, 70), n.node("n2", 20, 70)], FOOTWAY)
        n.way("east", [n.node("e1", 62, -20), n.node("e2", 62, 20)], FOOTWAY)
        graph = n.graph()

        snap = graph.snap_to_edge(*at(0, 0), accept=_walkable, max_distance_m=150.0)
        assert snap is not None
        assert snap.distance_m == pytest.approx(62.0, abs=1.0)

    def test_nothing_usable_within_the_limit_is_still_no_snap(self) -> None:
        n = Network()
        n.way("cycleway", [n.node("c1", -50, 20), n.node("c2", 50, 20)], CLOSED_CYCLEWAY)
        n.way("footway", [n.node("f1", -50, -170), n.node("f2", 50, -170)], FOOTWAY)
        graph = n.graph()

        assert graph.snap_to_edge(*at(0, 0), accept=_walkable, max_distance_m=150.0) is None

    def test_a_step_free_end_moves_past_stairs_to_a_path_90_m_away(self) -> None:
        # The same sweep decides where a step-free end moves to. A terrace and
        # its stairway within 60 m hid the only step-free path, 90 m south, and
        # the answer was "no step-free path joins these points".
        n = Network()
        terrace_west, terrace_east = n.node("t1", -20, 0), n.node("t2", 20, 0)
        foot = n.node("s", 20, -90)
        n.way("terrace", [terrace_west, terrace_east], FOOTWAY)
        n.way("stairs", [terrace_east, foot], {"highway": "steps"})
        n.way("street", [n.node("w", -100, -90), foot, n.node("e", 400, -90)], FOOTWAY)
        graph = n.graph()

        comparison = compare_routes(
            graph, origin=at(0, 1), destination=at(400, -90), profile=WHEELCHAIR
        )
        accessible = comparison.accessible_route
        assert accessible is not None, comparison.accessible_failure
        assert accessible.stairway_count == 0
        assert accessible.origin.distance_m == pytest.approx(91.0, abs=1.5)
        assert any(c.code == "profile_route_moved_endpoint" for c in comparison.cautions)
