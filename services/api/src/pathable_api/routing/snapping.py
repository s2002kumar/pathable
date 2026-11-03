"""Joining a requested coordinate to the network.

Snapping to the nearest *junction* was the old behaviour and it was wrong in a
specific, visible way: on a 300 m block the nearest junction can be 150 m from
the door somebody actually asked about, so the route began somewhere they were
not, and the distance it reported was a distance they would never walk.

Here a request attaches to the nearest point **along a segment**, wherever that
falls. The segment is split for this request only — the cached graph is never
mutated, because it is shared between concurrent requests and keyed on a dataset
version that is supposed to be immutable.

Two things have to be got right for the split to be honest:

* **Direction survives.** Half of a one-way segment is still one-way, and the
  half that runs against the permitted direction must not appear.
* **Cost is prorated, not recomputed.** A 40 m piece of a 200 m gravel path is
  a fifth of the gravel, not a fresh segment with the same fixed penalties. The
  flat penalties of a crossing or a stairway are already spread over its length
  (see `routing.units`), so a piece carries the share of them its length covers.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import TYPE_CHECKING

from shapely.geometry import LineString, Point
from shapely.ops import substring
from shapely.strtree import STRtree

from pathable_api.geo.geometry import geodesic_distance_m, geodesic_length_m

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pathable_api.geo.features import EdgeFeatures
    from pathable_api.routing.graph import DirectedEdge, RoutableEdge

#: Identifies the temporary node a snap introduces. Prefixed so it can never
#: collide with an OSM node id, which is always numeric.
VIRTUAL_NODE_PREFIX = "pathable:snap:"


@dataclass(frozen=True, slots=True)
class EdgeSnap:
    """Where a request joined the network, and on which segment."""

    edge: RoutableEdge
    #: Position along the segment, 0.0 at ``source_u`` and 1.0 at ``source_v``.
    fraction: float
    longitude: float
    latitude: float
    #: Straight-line distance from the requested point to where it attached.
    distance_m: float
    #: Set when the snap landed exactly on an existing junction, in which case no
    #: split is needed and routing can start from that node directly.
    node_id: str | None = None

    @property
    def point(self) -> Point:
        return Point(self.longitude, self.latitude)

    @property
    def is_on_node(self) -> bool:
        return self.node_id is not None


class EdgeIndex:
    """A spatial index over segment geometry, built once per dataset.

    A linear scan over every segment was tolerable at ten thousand nodes and is
    not at city scale. Shapely's STRtree is the right tool and costs one build
    per dataset — which the dataset-version cache already amortises.
    """

    __slots__ = ("_edges", "_geometries", "_tree")

    def __init__(self, edges: list[RoutableEdge]) -> None:
        self._edges = edges
        self._geometries = [edge.geometry for edge in edges]
        self._tree = STRtree(self._geometries) if self._geometries else None

    def __len__(self) -> int:
        return len(self._edges)

    def nearest(
        self,
        longitude: float,
        latitude: float,
        *,
        accept: Callable[[RoutableEdge], bool] | None = None,
        max_distance_m: float | None = None,
    ) -> EdgeSnap | None:
        """Closest point on any usable segment to a requested coordinate.

        ``accept`` decides what "usable" means, and it is not optional in
        practice. Snapping to the geometrically nearest segment regardless of
        whether the traveller may use it produced a real failure on the Waterloo
        network: a point in Waterloo Park landed 12 m away on a `foot=no`
        cycleway, which is correctly closed to pedestrians, so every route to
        that point failed — while a walkable path sat a few metres further on.
        "No route exists" and "the nearest line to your finger is a cycleway"
        are very different answers, and only one of them is true.

        The index works in degrees, which are not metres: a degree of longitude
        is about 73% of a degree of latitude here. So it only gathers
        candidates, inside a window wide enough to hold every segment within a
        distance in metres, and the winner is chosen by geodesic measurement. A
        winner further away than the window's distance is not trusted, because a
        segment just outside the window can be nearer; the window widens instead.
        Stopping at the first window that held any segment at all let closed
        paths within 60 m hide an open one 100 m away, inside the snapping limit.
        """
        if self._tree is None:
            return None

        point = Point(longitude, latitude)

        # The nearest segment in degrees is rarely far from the nearest in
        # metres, so a first window of its real distance almost always settles
        # it: the common case costs one small query, not a sweep.
        planar = self._tree.query_nearest(point, return_distance=False, all_matches=True)
        first_m = min(_project_onto(self._edges[int(index)], point).distance_m for index in planar)
        radii = (max(first_m, _SMALLEST_WINDOW_M), *(r for r in _SEARCH_RADII_M if r > first_m))

        best: EdgeSnap | None = None
        for radius_m in radii:
            best = self._best_of(
                point,
                self._within(point, radius_m),
                accept=accept,
                max_distance_m=max_distance_m,
            )
            if best is not None and best.distance_m <= radius_m:
                return best
            if max_distance_m is not None and radius_m >= max_distance_m:
                # Anything usable within the limit was inside this window and
                # would have been returned.
                return None
        return best

    def _best_of(
        self,
        point: Point,
        shortlist: list[int],
        *,
        accept: Callable[[RoutableEdge], bool] | None,
        max_distance_m: float | None,
    ) -> EdgeSnap | None:
        best: EdgeSnap | None = None
        for index in shortlist:
            edge = self._edges[index]
            if accept is not None and not accept(edge):
                continue
            snap = _project_onto(edge, point)
            if max_distance_m is not None and snap.distance_m > max_distance_m:
                continue
            if best is None or snap.distance_m < best.distance_m:
                best = snap
        return best

    def _within(self, point: Point, radius_m: float) -> list[int]:
        """Every segment that could be within `radius_m` metres, and some that are not.

        The window is a circle in degrees whose radius is the distance in
        degrees of longitude, the shorter axis, so it covers the distance north
        and south with room to spare.
        """
        per_degree = _METRES_PER_DEGREE_LATITUDE * math.cos(math.radians(point.y))
        radius = radius_m * _WINDOW_MARGIN / per_degree
        found = self._tree.query(point.buffer(radius))  # type: ignore[union-attr]
        return [int(index) for index in found]


#: Widening windows. Most points resolve in the first, and the last is the point
#: at which "there is nothing walkable near here" is the honest answer rather
#: than a reason to keep looking.
_SEARCH_RADII_M: tuple[float, ...] = (60.0, 200.0, 500.0)

#: The first window is never smaller than this, so a point lying exactly on a
#: segment still has a window to look in.
_SMALLEST_WINDOW_M = 1.0

#: A degree of latitude, in metres, on the sphere the window is sized on.
_METRES_PER_DEGREE_LATITUDE = 111_320.0

#: The sphere is not the ellipsoid geodesic distances are measured on; a 2%
#: wider window absorbs the difference.
_WINDOW_MARGIN = 1.02


def _project_onto(edge: RoutableEdge, point: Point) -> EdgeSnap:
    """Closest point on one segment, measured properly."""
    geometry = edge.geometry
    # `project`/`interpolate` work in the geometry's own units (degrees), which
    # is fine for locating the position; the *distance* is then measured
    # geodesically, because degrees are not metres.
    raw_fraction = geometry.project(point, normalized=True)
    fraction = min(1.0, max(0.0, float(raw_fraction)))
    landing = geometry.interpolate(fraction, normalized=True)

    return EdgeSnap(
        edge=edge,
        fraction=fraction,
        longitude=float(landing.x),
        latitude=float(landing.y),
        distance_m=geodesic_distance_m(point, Point(landing.x, landing.y)),
    )


def split_at(geometry: LineString, fractions: Sequence[float]) -> list[LineString]:
    """Cut a linestring at ascending normalised positions, keeping every vertex.

    Returns one more piece than positions, in the geometry's own order. Each cut
    lands exactly where a snap at that position does, so pieces meet their snap
    points. Truncating a piece to its two ends would straighten it and
    understate its length, which then understates every length-proportional
    cost on it. Two positions at one point give a zero-length piece between
    them, which is what two ends at one point should walk.
    """
    bounds = [0.0, *fractions, 1.0]
    landings = [geometry.interpolate(fraction, normalized=True) for fraction in bounds]
    pieces: list[LineString] = []
    for (start, end), (first, last) in zip(pairwise(bounds), pairwise(landings), strict=True):
        cut = substring(geometry, start, end, normalized=True)
        inner = list(cut.coords)[1:-1] if isinstance(cut, LineString) else []
        pieces.append(LineString([(first.x, first.y), *inner, (last.x, last.y)]))
    return pieces


def split_geometry(geometry: LineString, fraction: float) -> tuple[LineString, LineString]:
    """Cut a linestring in two at a normalised position. See `split_at`."""
    head, tail = split_at(geometry, [fraction])
    return head, tail


def prorate(directed: DirectedEdge, geometry: LineString) -> DirectedEdge:
    """Build a partial segment whose attributes are honest for its length.

    Length-proportional facts carry over unchanged, because they are true of the
    piece as much as the whole: a 40 m stretch of a 200 m gravel path really is
    gravel, at that gradient, with that missing data. Only the length changes,
    and every length-proportional cost follows from it.

    A crossing's or a stairway's flat penalties are spread over its length by
    `unit_share`, so a piece takes the share its length covers: the pieces of a
    segment add up to the segment, and a route walking part of a crossing pays
    for that part. A piece used to keep the whole segment's share, which charged
    a metre of a crossing for all of it and charged a crossing twice when both
    ends of a route lay on it. What the piece *is* does not change: a piece of a
    stairway is a stairway to every hard limit, and a piece of a crossing has
    the crossing's kerb.
    """
    from pathable_api.routing.graph import DirectedEdge as _DirectedEdge
    from pathable_api.routing.graph import RoutableEdge as _RoutableEdge

    length_m = geodesic_length_m(geometry)
    whole_m = geodesic_length_m(directed.edge.geometry)
    share = min(1.0, length_m / whole_m) if whole_m > 0 else 1.0
    partial = _RoutableEdge(
        edge_id=directed.edge.edge_id,
        source_u=directed.edge.source_u,
        source_v=directed.edge.source_v,
        edge_key=directed.edge.edge_key,
        # A snap landing exactly on a vertex can produce a zero-length piece.
        # Zero would make every length-proportional cost vanish.
        length_m=max(length_m, 0.01),
        geometry=geometry,
        features=_part_of(directed.edge.features, share),
        name=directed.edge.name,
        foot_forward=directed.edge.foot_forward,
        foot_backward=directed.edge.foot_backward,
        source_way_id=directed.edge.source_way_id,
    )
    return _DirectedEdge(
        edge=partial, features=_part_of(directed.features, share), reversed=directed.reversed
    )


def _part_of(features: EdgeFeatures, share: float) -> EdgeFeatures:
    return replace(features, unit_share=features.unit_share * share)


def virtual_node_id(label: str) -> str:
    """A node id for a snap point that cannot collide with an OSM id."""
    return f"{VIRTUAL_NODE_PREFIX}{label}:{uuid.uuid4().hex[:8]}"


__all__ = [
    "VIRTUAL_NODE_PREFIX",
    "EdgeIndex",
    "EdgeSnap",
    "prorate",
    "split_at",
    "split_geometry",
    "virtual_node_id",
]
