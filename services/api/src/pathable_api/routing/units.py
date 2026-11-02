"""One crossing is one crossing; one stairway is one stairway.

OpenStreetMap draws a road crossing as one way from kerb to kerb, and a stairway
as one way from bottom to top. The network keeps every vertex of a way as a
junction, so a way arrives as one segment per node pair. On the Waterloo
dataset 5,430 of 5,682 crossing ways arrive as two or more segments (11,764 in
all), and so do 41 of 374 stairways.

Treating each piece as a crossing or a stairway of its own did three wrong
things, all of them visible on real routes:

* **Flat penalties were charged once per piece.** A crossing's penalty is a
  fixed amount per crossing, so a crossing in three pieces cost three times
  what one in one piece did, and a stairway's per-step charge was applied to its
  whole step count once per piece.
* **The middle of a crossing had no kerb.** A kerb is recorded on the node where
  the crossing meets the sidewalk, so the piece in the middle of the road
  touches no kerb node and read as "no kerb recorded" — on 424 crossing ways
  whose kerbs are recorded at both ends. The wheelchair route detoured around
  crossings it had good evidence for.
* **Counts shown to people counted pieces.** "4 crossings", "4 stairways",
  "20 steps" were numbers of segments, not of crossings, stairways or steps.

So each crossing or stairway way becomes one *unit*. Its flat penalties are
spread over its pieces in proportion to their length: walking the whole unit
pays each penalty exactly once, and costs stay a sum over segments, so the
search and its heuristic are unchanged. A crossing's kerb is read from every
kerb recorded anywhere along it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from pathable_api.geo.enums import KerbType, TriState
from pathable_api.geo.features import EdgeFeatures
from pathable_api.geo.node_evidence import read_node_evidence, worst_kerb

if TYPE_CHECKING:
    # The graph builds its segments with this module, so the import is one-way.
    from pathable_api.routing.graph import RoutableEdge

UnitKind = Literal["crossing", "stairway"]


def unit_kind(features: EdgeFeatures) -> UnitKind | None:
    """Whether a segment belongs to a crossing, a stairway, or neither."""
    if features.is_crossing:
        return "crossing"
    if features.steps is TriState.YES:
        return "stairway"
    return None


def recorded_node_kerbs(
    node_tags: Iterable[tuple[str, Mapping[str, object]]],
) -> dict[str, KerbType]:
    """Kerbs recorded on nodes, by node id. Nodes that record none are left out."""
    kerbs: dict[str, KerbType] = {}
    for node_id, tags in node_tags:
        kerb = read_node_evidence(dict(tags)).kerb
        if kerb is not KerbType.UNKNOWN:
            kerbs[node_id] = kerb
    return kerbs


@dataclass(frozen=True, slots=True)
class CrossingKerb:
    """What is recorded about the kerbs of one crossing."""

    kerb: KerbType
    #: Ends with a recorded kerb: 0, 1, or 2 for two or more records.
    recorded_ends: int


def crossing_kerb(
    pieces: Sequence[RoutableEdge], node_kerbs: Mapping[str, KerbType]
) -> CrossingKerb:
    """The kerb of a whole crossing.

    A kerb tagged on the crossing way itself describes the whole crossing, as it
    does at ingestion. Otherwise every kerb node anywhere along the way counts:
    two or more recorded kerbs describe both ends, and the crossing has the worst
    of them; a single recorded kerb leaves the other end unknown, so the
    crossing is no better than unknown; none at all is unknown.

    The single-kerb rule is stricter than ingestion's, which took a lone dropped
    kerb as the crossing's kerb when the crossing happened to be one segment. A
    kerb nobody recorded is not a dropped one.
    """
    on_way = [
        piece.features.kerb
        for piece in pieces
        if piece.features.kerb is not KerbType.UNKNOWN and not piece.features.kerb_from_node
    ]
    if on_way:
        return CrossingKerb(worst_kerb(on_way), 2)

    nodes = sorted({node for piece in pieces for node in (piece.source_u, piece.source_v)})
    recorded = [node_kerbs[node] for node in nodes if node in node_kerbs]
    if not recorded:
        return CrossingKerb(KerbType.UNKNOWN, 0)
    if len(recorded) == 1:
        return CrossingKerb(worst_kerb((recorded[0], KerbType.UNKNOWN)), 1)
    return CrossingKerb(worst_kerb(recorded), 2)


def _unit_ref(kind: UnitKind, edge: RoutableEdge) -> str:
    # The way is the unit. A segment whose way is unknown is its own unit, which
    # is the old behaviour and the only safe one.
    if edge.source_way_id:
        return f"{kind}:way/{edge.source_way_id}"
    return f"{kind}:{edge.identity}"


def assign_units(
    segments: Sequence[RoutableEdge], node_kerbs: Mapping[str, KerbType]
) -> list[RoutableEdge]:
    """Mark every crossing and stairway piece with its unit, share and kerb.

    Returns a new list; segments that are neither are returned as they were.
    """
    groups: dict[str, list[int]] = {}
    for index, segment in enumerate(segments):
        kind = unit_kind(segment.features)
        if kind is not None:
            groups.setdefault(_unit_ref(kind, segment), []).append(index)

    result = list(segments)
    for ref, indexes in groups.items():
        pieces = [segments[index] for index in indexes]
        total = sum(piece.length_m for piece in pieces)
        kerb = crossing_kerb(pieces, node_kerbs) if ref.startswith("crossing:") else None
        for index, piece in zip(indexes, pieces, strict=True):
            share = piece.length_m / total if total > 0 else 1.0 / len(pieces)
            features = replace(
                piece.features,
                unit_ref=ref,
                unit_share=share,
                crossing_kerb=None if kerb is None else kerb.kerb,
                crossing_kerb_ends=None if kerb is None else kerb.recorded_ends,
            )
            result[index] = replace(piece, features=features)
    return result


@dataclass(frozen=True, slots=True)
class UnitTraversal:
    """One crossing or stairway as a route passes through it."""

    kind: UnitKind
    ref: str
    #: Positions in the route's segments.
    segment_indexes: tuple[int, ...]


def traversals(features: Sequence[EdgeFeatures]) -> list[UnitTraversal]:
    """The crossings and stairways a sequence of segments passes through, in order.

    Consecutive pieces of one unit are one traversal. A route that leaves a unit
    and later comes back to it passes through it twice, and says so.
    """
    found: list[UnitTraversal] = []
    current: tuple[UnitKind, str, list[int]] | None = None
    for index, item in enumerate(features):
        kind = unit_kind(item)
        ref = item.unit_ref or (None if kind is None else f"{kind}:#{index}")
        if kind is not None and current is not None and current[0] == kind and current[1] == ref:
            current[2].append(index)
            continue
        if current is not None:
            found.append(UnitTraversal(current[0], current[1], tuple(current[2])))
            current = None
        if kind is not None and ref is not None:
            current = (kind, ref, [index])
    if current is not None:
        found.append(UnitTraversal(current[0], current[1], tuple(current[2])))
    return found
