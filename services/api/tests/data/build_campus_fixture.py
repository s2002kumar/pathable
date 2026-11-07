"""Build the sealed University of Waterloo campus extract the full-stack CI job routes on.

Run once, by hand, from services/api:

    uv run python -m tests.data.build_campus_fixture <overpass.osm> <out.osm.pbf> <snapshot>

``<overpass.osm>`` is the response to the Overpass query in tests/data/README.md
and ``<snapshot>`` the ``osm_base`` time it reported. The output keeps every
node inside the box, every way that touches it (whole, so no way is cut), and
the building multipolygons among the relations with their member ways. Route,
boundary and transit relations are dropped: they reach across southern Ontario
and nothing on campus needs them. Contributor names and ids are dropped too;
versions and timestamps are kept, because element provenance reads them.

The header carries the snapshot time, which is where the place index takes its
"OpenStreetMap as of" date from.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

import osmium

#: south, west, north, east — as in README.md's query.
BOX: Final = (43.4690, -80.5490, 43.4755, -80.5385)


def _inside(lat: float, lon: float) -> bool:
    south, west, north, east = BOX
    return south <= lat <= north and west <= lon <= east


def build(source: Path, target: Path, snapshot: str) -> tuple[int, int, int]:
    inside: set[int] = set()
    way_nodes: dict[int, list[int]] = {}
    relations: dict[int, tuple[dict[str, str], list[tuple[str, int]]]] = {}
    for obj in osmium.FileProcessor(str(source)):
        if isinstance(obj, osmium.osm.Node):
            if _inside(obj.location.lat, obj.location.lon):
                inside.add(obj.id)
        elif isinstance(obj, osmium.osm.Way):
            way_nodes[obj.id] = [ref.ref for ref in obj.nodes]
        elif isinstance(obj, osmium.osm.Relation):
            relations[obj.id] = (
                {tag.k: tag.v for tag in obj.tags},
                [(member.type, member.ref) for member in obj.members],
            )

    ways = {way for way, refs in way_nodes.items() if any(ref in inside for ref in refs)}
    kept_relations: set[int] = set()
    for relation, (tags, members) in relations.items():
        is_building = "building" in tags and tags.get("type") in {"multipolygon", "building"}
        member_ways = [ref for kind, ref in members if kind == "w"]
        if is_building and any(ref in ways for ref in member_ways):
            kept_relations.add(relation)
            ways.update(ref for ref in member_ways if ref in way_nodes)
    nodes = inside | {ref for way in ways for ref in way_nodes[way]}

    header = osmium.io.Header()
    header.set("osmosis_replication_timestamp", snapshot)
    header.set("generator", "pathable build_campus_fixture (Overpass API extract)")
    writer = osmium.SimpleWriter(str(target), header=header, overwrite=True)
    try:
        for obj in osmium.FileProcessor(str(source)):
            if isinstance(obj, osmium.osm.Node) and obj.id in nodes:
                writer.add_node(obj.replace(user="", uid=0, changeset=0))
            elif isinstance(obj, osmium.osm.Way) and obj.id in ways:
                writer.add_way(obj.replace(user="", uid=0, changeset=0))
            elif isinstance(obj, osmium.osm.Relation) and obj.id in kept_relations:
                writer.add_relation(obj.replace(user="", uid=0, changeset=0))
    finally:
        writer.close()
    return len(nodes), len(ways), len(kept_relations)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        sys.stderr.write("usage: build_campus_fixture <overpass.osm> <out.osm.pbf> <snapshot>\n")
        return 2
    nodes, ways, relations = build(Path(argv[1]), Path(argv[2]), argv[3])
    sys.stdout.write(f"nodes {nodes}, ways {ways}, building relations {relations}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
