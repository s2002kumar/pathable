"""Measuring how fast routing actually is.

Exists so performance claims come from a run somebody can repeat, not from an
estimate. Every number this produces is a wall-clock measurement on the machine
it ran on, and the report says which dataset it measured — a figure from the
nine-node synthetic fixture and a figure from a real city network are not
comparable, and neither should be quoted as the other.
"""

from __future__ import annotations

import math
import random
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from shapely.geometry import LineString, Point

from pathable_api.geo.features import normalise_edge
from pathable_api.geo.network import NetworkEdge, NetworkNode, NetworkPayload
from pathable_api.routing.comparison import compare_routes
from pathable_api.routing.engine import RoutingError, compute_route
from pathable_api.routing.graph import RoutableGraph
from pathable_api.routing.profiles import MobilityProfile
from pathable_api.routing.search import Algorithm

if TYPE_CHECKING:
    from pathable_api.routing.evaluation import RouteCase


@dataclass(slots=True)
class LatencySummary:
    """Wall-clock milliseconds for one profile over one sample set."""

    profile: str
    samples: int
    failures: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    mean_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "samples": self.samples,
            "failures": self.failures,
            "p50_ms": round(self.p50_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "max_ms": round(self.max_ms, 2),
            "mean_ms": round(self.mean_ms, 2),
        }


@dataclass(slots=True)
class BenchmarkReport:
    dataset: str
    node_count: int
    segment_count: int
    summaries: list[LatencySummary] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "node_count": self.node_count,
            "segment_count": self.segment_count,
            "profiles": [summary.to_dict() for summary in self.summaries],
        }


def measure(
    graph: RoutableGraph,
    profiles: list[MobilityProfile],
    *,
    samples: int = 50,
    seed: int = 20260812,
    dataset_label: str | None = None,
) -> BenchmarkReport:
    """Time route computation over randomly chosen node pairs.

    The same seed produces the same pairs, so two runs are comparable and a
    regression is a real change rather than a different set of journeys.
    """
    # Deterministic by design: the point is that two runs measure the same
    # journeys. Nothing here is a secret.
    rng = random.Random(seed)  # noqa: S311
    node_ids = sorted(graph.node_positions)
    if len(node_ids) < 2:
        msg = "A benchmark needs at least two nodes to route between."
        raise ValueError(msg)

    pairs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    while len(pairs) < samples:
        origin_id, destination_id = rng.sample(node_ids, 2)
        pairs.append((graph.node_positions[origin_id], graph.node_positions[destination_id]))

    report = BenchmarkReport(
        dataset=dataset_label or f"{graph.region_slug}:{graph.checksum[:12]}",
        node_count=graph.node_count,
        segment_count=graph.segment_count,
    )

    for profile in profiles:
        durations: list[float] = []
        failures = 0
        for origin, destination in pairs:
            started = time.perf_counter()
            try:
                compute_route(graph, origin=origin, destination=destination, profile=profile)
            except RoutingError:
                # A journey with no possible route is a real outcome, not an
                # error to hide — but its timing says nothing about routing
                # speed, so it is counted rather than averaged in.
                failures += 1
                continue
            durations.append((time.perf_counter() - started) * 1000.0)

        report.summaries.append(_summarise(profile.key, durations, failures))

    return report


def _summarise(profile: str, durations: list[float], failures: int) -> LatencySummary:
    if not durations:
        return LatencySummary(profile, 0, failures, 0.0, 0.0, 0.0, 0.0)

    ordered = sorted(durations)
    return LatencySummary(
        profile=profile,
        samples=len(ordered),
        failures=failures,
        p50_ms=statistics.median(ordered),
        p95_ms=ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
        max_ms=ordered[-1],
        mean_ms=statistics.fmean(ordered),
    )


#: How the corpus measurement is taken, written into its report so a reader
#: never has to guess what the numbers mean.
CORPUS_METHOD = (
    "Warm, single process: the graph is loaded and its spatial index built (timed "
    "separately) before any route is timed, and every journey is routed once per "
    "profile and algorithm before timing. Each timed run is one compute_route call - "
    "snapping, the request-local split, the search and building the route's segments "
    "- measured with time.perf_counter. Dijkstra and A* alternate within each repeat "
    "so machine noise falls on both. Per journey the median of the repeats is kept; "
    "the summary's percentiles are over those per-journey medians. Agreement means "
    "both algorithms found a route of the same effective cost (within 0.01) or both "
    "found none. The comparison timing is compare_routes with A*: both routes and "
    "the explanation, without HTTP or JSON."
)


@dataclass(slots=True)
class CorpusRun:
    """One journey, one profile, one algorithm: what it cost and how long it took."""

    case: str
    profile: str
    algorithm: str
    runs_ms: list[float]
    routed: bool
    expanded_nodes: int | None = None
    effective_cost: float | None = None
    distance_m: float | None = None

    @property
    def median_ms(self) -> float | None:
        return statistics.median(self.runs_ms) if self.runs_ms else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "profile": self.profile,
            "algorithm": self.algorithm,
            "routed": self.routed,
            "median_ms": None if self.median_ms is None else round(self.median_ms, 2),
            "runs_ms": [round(run, 2) for run in self.runs_ms],
            "expanded_nodes": self.expanded_nodes,
            "effective_cost": (
                None if self.effective_cost is None else round(self.effective_cost, 3)
            ),
            "distance_m": None if self.distance_m is None else round(self.distance_m, 1),
        }


def _timed_route(
    graph: RoutableGraph,
    case: RouteCase,
    profile: MobilityProfile,
    algorithm: Algorithm,
) -> tuple[float, Any]:
    started = time.perf_counter()
    try:
        route = compute_route(
            graph,
            origin=case.origin,
            destination=case.destination,
            profile=profile,
            algorithm=algorithm,
        )
    except RoutingError:
        route = None
    return (time.perf_counter() - started) * 1000.0, route


def measure_corpus(
    graph: RoutableGraph,
    cases: Sequence[RouteCase],
    profiles: Sequence[MobilityProfile],
    *,
    repeats: int = 5,
    compare_profile: MobilityProfile | None = None,
) -> dict[str, Any]:
    """Time both searches, and the whole comparison, on a fixed corpus of journeys.

    The same journeys every time, so two runs measure the same work. See
    `CORPUS_METHOD` for exactly what is timed.
    """
    started = time.perf_counter()
    _ = graph.edge_index
    index_ms = (time.perf_counter() - started) * 1000.0

    algorithms = (Algorithm.DIJKSTRA, Algorithm.ASTAR)
    for case in cases:
        for profile in profiles:
            for algorithm in algorithms:
                _timed_route(graph, case, profile, algorithm)

    runs: list[CorpusRun] = []
    for case in cases:
        for profile in profiles:
            measured = {
                algorithm: CorpusRun(case.key, profile.key, algorithm.value, [], routed=False)
                for algorithm in algorithms
            }
            for _ in range(repeats):
                for algorithm in algorithms:
                    elapsed, route = _timed_route(graph, case, profile, algorithm)
                    run = measured[algorithm]
                    if route is None:
                        continue
                    run.runs_ms.append(elapsed)
                    run.routed = True
                    run.expanded_nodes = route.expanded_nodes
                    run.effective_cost = route.effective_distance_m
                    run.distance_m = route.distance_m
            runs.extend(measured.values())

    comparisons: list[dict[str, Any]] = []
    if compare_profile is not None:
        for case in cases:
            timings: list[float] = []
            for _ in range(repeats):
                started = time.perf_counter()
                compare_routes(
                    graph,
                    origin=case.origin,
                    destination=case.destination,
                    profile=compare_profile,
                )
                timings.append((time.perf_counter() - started) * 1000.0)
            comparisons.append(
                {
                    "case": case.key,
                    "profile": compare_profile.key,
                    "median_ms": round(statistics.median(timings), 2),
                    "runs_ms": [round(timing, 2) for timing in timings],
                }
            )

    return {
        "method": CORPUS_METHOD,
        "repeats": repeats,
        "spatial_index_build_ms": round(index_ms, 1),
        "summary": _corpus_summary(runs, profiles, comparisons),
        "runs": [run.to_dict() for run in runs],
        "comparisons": comparisons,
    }


def _percentiles(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "p50_ms": round(statistics.median(ordered), 2),
        # Nearest rank: with twenty journeys this is the nineteenth.
        "p95_ms": round(ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)], 2),
        "max_ms": round(ordered[-1], 2),
    }


def _corpus_summary(
    runs: Sequence[CorpusRun],
    profiles: Sequence[MobilityProfile],
    comparisons: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    summary: dict[str, Any] = {"by_profile": {}}
    agreements = disagreements = 0
    for profile in profiles:
        mine = [run for run in runs if run.profile == profile.key]
        per_algorithm: dict[str, Any] = {}
        for algorithm in (Algorithm.DIJKSTRA, Algorithm.ASTAR):
            routed = [
                run
                for run in mine
                if run.algorithm == algorithm.value and run.median_ms is not None
            ]
            expanded = [run.expanded_nodes for run in routed if run.expanded_nodes is not None]
            per_algorithm[algorithm.value] = {
                **_percentiles([run.median_ms for run in routed if run.median_ms is not None]),
                "median_expanded_nodes": statistics.median(expanded) if expanded else None,
            }
        by_case: dict[str, dict[str, CorpusRun]] = {}
        for run in mine:
            by_case.setdefault(run.case, {})[run.algorithm] = run
        for pair in by_case.values():
            dijkstra, astar = pair[Algorithm.DIJKSTRA.value], pair[Algorithm.ASTAR.value]
            same = dijkstra.routed == astar.routed and (
                not dijkstra.routed
                or abs((dijkstra.effective_cost or 0.0) - (astar.effective_cost or 0.0)) < 0.01
            )
            if same:
                agreements += 1
            else:
                disagreements += 1
        summary["by_profile"][profile.key] = per_algorithm
    summary["cost_agreement"] = {"agree": agreements, "disagree": disagreements}
    summary["comparison"] = _percentiles([float(item["median_ms"]) for item in comparisons])
    return summary


def build_measurement_grid(
    size: int, *, origin: tuple[float, float] = (-80.54, 43.47)
) -> NetworkPayload:
    """A regular lattice, for measuring how routing scales.

    **Not a map of anywhere.** It exists purely so latency can be characterised
    against a network of a chosen size when a real one is unavailable, and any
    number derived from it must be reported as what it is.

    Every fourth segment is stairs and every third has no recorded surface, so
    the cost model and the uncertainty penalty are exercised rather than
    measured on a uniform network where every edge costs the same.
    """
    step = 0.0006  # ~50 m north-south
    nodes: list[NetworkNode] = []
    for row in range(size):
        for column in range(size):
            nodes.append(
                NetworkNode(
                    source_node_id=f"n{row}_{column}",
                    geometry=Point(origin[0] + column * step, origin[1] + row * step),
                )
            )

    position = {node.source_node_id: (node.longitude, node.latitude) for node in nodes}
    edges: list[NetworkEdge] = []
    index = 0
    for row in range(size):
        for column in range(size):
            for neighbour_row, neighbour_column in ((row, column + 1), (row + 1, column)):
                if neighbour_row >= size or neighbour_column >= size:
                    continue
                u = f"n{row}_{column}"
                v = f"n{neighbour_row}_{neighbour_column}"
                tags: dict[str, Any] = {"highway": "footway"}
                if index % 4 == 0:
                    tags = {"highway": "steps", "step_count": "12"}
                elif index % 3 == 0:
                    tags = {"highway": "footway"}  # no surface recorded
                else:
                    tags = {"highway": "footway", "surface": "asphalt", "smoothness": "good"}

                edges.append(
                    NetworkEdge(
                        source_u=u,
                        source_v=v,
                        edge_key=0,
                        geometry=LineString([position[u], position[v]]),
                        features=normalise_edge(tags),
                        source_way_id=f"grid-{index}",
                    )
                )
                index += 1

    return NetworkPayload(nodes=nodes, edges=edges)
