"""One crossing is one crossing; one stairway is one stairway (routing policy 3).

The network keeps every vertex of an OpenStreetMap way as a junction, so a
crossing drawn from kerb to kerb through the road's centreline arrives as two
or more segments. These tests pin what that must not change: what a crossing or
a stairway costs, what its kerb is, and how many of them a route reports.
"""

from __future__ import annotations

import math
from itertools import pairwise
from typing import Any

import pytest
from shapely.geometry import LineString, Point

from pathable_api.geo.enums import KerbType
from pathable_api.geo.features import normalise_edge
from pathable_api.geo.network import NetworkEdge, NetworkNode, NetworkPayload
from pathable_api.routing.comparison import compare_routes
from pathable_api.routing.cost import CostCode, evaluate_edge
from pathable_api.routing.engine import compute_route
from pathable_api.routing.graph import RoutableGraph, graph_from_payload
from pathable_api.routing.profiles import STANDARD, get_profile
from pathable_api.routing.search import Algorithm
from pathable_api.routing.units import traversals

LON, LAT = -80.53, 43.47
WHEELCHAIR = get_profile("wheelchair")
CRUTCHES = get_profile("crutches")

SIDEWALK = {"highway": "footway", "footway": "sidewalk"}
CROSSING = {"highway": "footway", "footway": "crossing"}
LOWERED = {"barrier": "kerb", "kerb": "lowered"}
RAISED = {"barrier": "kerb", "kerb": "raised"}


def at(east_m: float, north_m: float) -> tuple[float, float]:
    per_lon = 111_320.0 * math.cos(math.radians(LAT))
    return (LON + east_m / per_lon, LAT + north_m / 111_320.0)


class Network:
    """Invented ways in metres, cut at every node as the PBF import cuts them."""

    def __init__(self) -> None:
        self.nodes: dict[str, tuple[float, float]] = {}
        self.node_tags: dict[str, dict[str, Any]] = {}
        self.edges: list[NetworkEdge] = []

    def node(self, name: str, east: float, north: float, **tags: str) -> str:
        self.nodes[name] = (east, north)
        self.node_tags[name] = dict(tags)
        return name

    def way(self, way_id: str, refs: list[str], tags: dict[str, Any]) -> None:
        features = normalise_edge(dict(tags))
        for key, (u, v) in enumerate(pairwise(refs)):
            self.edges.append(
                NetworkEdge(
                    source_u=u,
                    source_v=v,
                    edge_key=key,
                    geometry=LineString([at(*self.nodes[u]), at(*self.nodes[v])]),
                    features=features,
                    source_way_id=way_id,
                )
            )

    def graph(self) -> RoutableGraph:
        nodes = [
            NetworkNode(
                source_node_id=name, geometry=Point(*at(*xy)), raw_tags=self.node_tags[name]
            )
            for name, xy in self.nodes.items()
        ]
        return graph_from_payload(NetworkPayload(nodes=nodes, edges=self.edges))


def crossing_network(
    *, south_kerb: dict[str, str], north_kerb: dict[str, str], pieces: int = 3
) -> tuple[Network, dict[str, str]]:
    """A south sidewalk and a north one joined by one 12 m crossing in `pieces` segments."""
    n = Network()
    south = n.node("s", 0, 0, **south_kerb)
    north = n.node("n", 0, 12, **north_kerb)
    middle = [n.node(f"m{i}", 0, 12 * i / pieces) for i in range(1, pieces)]
    west, east = n.node("sw", -50, 0), n.node("ne", 50, 12)
    n.way("sidewalk-south", [west, south], SIDEWALK)
    n.way("crossing", [south, *middle, north], CROSSING)
    n.way("sidewalk-north", [north, east], SIDEWALK)
    return n, {"west": west, "east": east}


def journey(n: Network, ends: dict[str, str]) -> tuple[tuple[float, float], tuple[float, float]]:
    return at(*n.nodes[ends["west"]]), at(*n.nodes[ends["east"]])


def crossing_pieces(graph: RoutableGraph) -> list[Any]:
    return [edge for edge in graph.segments if edge.features.is_crossing]


class TestACrossingIsOneUnit:
    def test_every_piece_carries_the_crossing_and_the_shares_add_up(self) -> None:
        n, _ = crossing_network(south_kerb=LOWERED, north_kerb=LOWERED)
        pieces = crossing_pieces(n.graph())

        assert len(pieces) == 3
        assert {piece.features.unit_ref for piece in pieces} == {"crossing:way/crossing"}
        assert sum(piece.features.unit_share for piece in pieces) == pytest.approx(1.0)

    def test_the_middle_of_a_crossing_has_the_crossings_kerb(self) -> None:
        # Regression: a kerb is recorded on the node at each end, so the piece in
        # the middle of the road touched no kerb node and read as "no kerb
        # recorded" — on 424 Waterloo crossings recorded as dropped at both ends.
        n, _ = crossing_network(south_kerb=LOWERED, north_kerb=LOWERED)
        pieces = crossing_pieces(n.graph())

        assert {piece.features.kerb_at_crossing for piece in pieces} == {KerbType.LOWERED}
        assert {piece.features.crossing_kerb_ends for piece in pieces} == {2}
        assert all("kerb" not in piece.features.unknown_attributes for piece in pieces)

    def test_one_recorded_end_leaves_the_crossing_unknown(self) -> None:
        # One dropped kerb says nothing about the other end. The same evidence
        # used to read as a dropped crossing when the crossing was one segment.
        for pieces in (1, 3):
            n, _ = crossing_network(south_kerb=LOWERED, north_kerb={}, pieces=pieces)
            found = crossing_pieces(n.graph())
            assert {piece.features.kerb_at_crossing for piece in found} == {KerbType.UNKNOWN}
            assert {piece.features.crossing_kerb_ends for piece in found} == {1}

    def test_a_crossing_has_its_worst_recorded_kerb(self) -> None:
        n, _ = crossing_network(south_kerb=LOWERED, north_kerb=RAISED)

        assert {piece.features.kerb_at_crossing for piece in crossing_pieces(n.graph())} == {
            KerbType.RAISED
        }

    def test_a_kerb_tagged_on_the_crossing_way_describes_all_of_it(self) -> None:
        n = Network()
        south, north = n.node("s", 0, 0), n.node("n", 0, 12)
        middle = n.node("m", 0, 6)
        n.way("crossing", [south, middle, north], {**CROSSING, "kerb": "flush"})

        pieces = crossing_pieces(n.graph())
        assert {piece.features.kerb_at_crossing for piece in pieces} == {KerbType.FLUSH}
        assert {piece.features.crossing_kerb_ends for piece in pieces} == {2}

    @pytest.mark.parametrize("pieces", [1, 2, 4])
    def test_walking_a_crossing_pays_its_penalties_once_however_it_is_drawn(
        self, pieces: int
    ) -> None:
        # Regression: flat penalties were charged once per segment, so a crossing
        # drawn in four pieces cost four times one drawn in one.
        n, ends = crossing_network(south_kerb={}, north_kerb={}, pieces=pieces)
        origin, destination = journey(n, ends)
        route = compute_route(n.graph(), origin=origin, destination=destination, profile=WHEELCHAIR)

        def paid(code: CostCode) -> float:
            return sum(
                component.effective_metres
                for segment in route.segments
                for component in segment.cost_components
                if component.code is code
            )

        assert paid(CostCode.KERB) == pytest.approx(WHEELCHAIR.kerb_penalty_m[KerbType.UNKNOWN])
        assert paid(CostCode.CROSSING) == pytest.approx(WHEELCHAIR.crossing_penalty_m)

    def test_a_route_counts_the_crossing_once(self) -> None:
        n, ends = crossing_network(south_kerb={}, north_kerb={}, pieces=3)
        origin, destination = journey(n, ends)
        route = compute_route(n.graph(), origin=origin, destination=destination, profile=STANDARD)

        assert sum(1 for segment in route.segments if segment.is_crossing) == 3
        assert route.crossing_count == 1
        assert route.unknown_kerb_crossing_count == 1


def stair_network(*, step_count: int | None, pieces: int) -> tuple[Network, dict[str, str]]:
    """A 20 m stairway in `pieces` segments, with a 140 m path round it."""
    n = Network()
    bottom, top = n.node("b", 0, 0), n.node("t", 20, 0)
    landings = [n.node(f"l{i}", 20 * i / pieces, 0) for i in range(1, pieces)]
    tags: dict[str, Any] = {"highway": "steps"}
    if step_count is not None:
        tags["step_count"] = str(step_count)
    n.way("stairs", [bottom, *landings, top], tags)
    detour = [n.node("d1", 0, 60), n.node("d2", 20, 60)]
    n.way("ramp", [bottom, *detour, top], {"highway": "footway", "surface": "asphalt"})
    return n, {"west": bottom, "east": top}


class TestAStairwayIsOneUnit:
    def test_its_step_count_is_charged_once(self) -> None:
        # Regression: a stairway's step count is the whole way's, and it was
        # charged in full on every piece — 17 split Waterloo stairways carry one.
        flat = CRUTCHES.step_penalty_m + CRUTCHES.per_step_penalty_m * 10
        for pieces in (1, 2, 3):
            n, _ = stair_network(step_count=10, pieces=pieces)
            stairs = [edge for edge in n.graph().segments if edge.features.unit_ref]
            charged = sum(
                component.effective_metres
                for edge in stairs
                for component in evaluate_edge(edge.features, edge.length_m, CRUTCHES).components
                if component.code is CostCode.STEPS
            )
            assert charged == pytest.approx(flat)

    def test_a_route_reports_one_stairway_and_its_steps_once(self) -> None:
        n, ends = stair_network(step_count=10, pieces=3)
        origin, destination = journey(n, ends)
        route = compute_route(n.graph(), origin=origin, destination=destination, profile=STANDARD)

        assert route.stairway_count == 1
        assert route.step_count == 10

    def test_a_wheelchair_comparison_says_one_stairway(self) -> None:
        n, ends = stair_network(step_count=None, pieces=3)
        origin, destination = journey(n, ends)
        comparison = compare_routes(
            n.graph(), origin=origin, destination=destination, profile=WHEELCHAIR
        )

        stairs = next(item for item in comparison.explanations if item.code == "avoids_stairs")
        assert stairs.evidence["stairway_count"] == 1
        assert stairs.evidence["stairways_without_step_count"] == 1
        assert comparison.summary.headline.endswith("to avoid 1 recorded stairway.")


class TestTraversals:
    def test_leaving_a_unit_and_coming_back_is_two_traversals(self) -> None:
        n, _ = crossing_network(south_kerb={}, north_kerb={}, pieces=2)
        graph = n.graph()
        crossing = [edge.features for edge in crossing_pieces(graph)]
        sidewalk = next(edge.features for edge in graph.segments if not edge.features.is_crossing)

        found = traversals([crossing[0], sidewalk, crossing[1]])
        assert [unit.segment_indexes for unit in found] == [(0,), (2,)]


class TestTheSearchStaysOptimal:
    def test_astar_agrees_with_dijkstra_on_split_crossings(self) -> None:
        # Shares only ever scale a non-negative penalty, so every edge still
        # costs at least its length and the straight-line heuristic stays a
        # lower bound.
        n, ends = crossing_network(south_kerb=LOWERED, north_kerb={}, pieces=4)
        graph = n.graph()
        origin, destination = journey(n, ends)
        for profile in (STANDARD, WHEELCHAIR, CRUTCHES):
            astar = compute_route(graph, origin=origin, destination=destination, profile=profile)
            dijkstra = compute_route(
                graph,
                origin=origin,
                destination=destination,
                profile=profile,
                algorithm=Algorithm.DIJKSTRA,
            )
            assert astar.effective_distance_m == pytest.approx(dijkstra.effective_distance_m)
