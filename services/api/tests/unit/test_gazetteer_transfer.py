"""A place-index artifact proves its own content before anything is written.

The artifact carries a production place index from where the extract is to the
host that serves it. These tests hold the format to that: a file that was cut
short, edited or miscounted is refused, and the same index always writes the
same bytes.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from typing import Any

import pytest

from pathable_api.geo.enums import PlaceKind
from pathable_api.geo.gazetteer_transfer import (
    ARTIFACT_FORMAT,
    PlaceIndexTransferError,
    read_place_index,
    write_place_index,
)

EXTRACT = "c" * 64


def record(kind: str, label: str, osm_id: int, **extra: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "category": "university" if kind == "place" else kind,
        "search_text": label.lower(),
        "longitude": -80.5419973,
        "latitude": 43.4724277,
        "osm_type": "way",
        "osm_id": osm_id,
        "osm_version": 7,
        "codes": [],
        "initialisms": [],
        **extra,
    }


RECORDS = [
    record("place", "William G. Davis Computer Research Centre", 1, codes=["dc"]),
    record("address", "200 University Avenue West, Waterloo", 2),
    record("street", "Ring Road", 3),
]


def header(**changes: Any) -> dict[str, Any]:
    return {
        "gazetteer_version": 2,
        "region": "waterloo",
        "source_name": "openstreetmap-pbf:waterloo",
        "file_name": "ontario-latest.osm.pbf",
        "file_sha256": EXTRACT,
        "source_timestamp": "2026-08-16T20:21:06+00:00",
        "built_at": "2026-11-02T17:41:34+00:00",
        "configuration": {"gazetteer_version": 2},
        "counts": {"place": 1, "address": 1, "street": 1},
        **changes,
    }


def lines_of(data: bytes) -> list[bytes]:
    return gzip.decompress(data).splitlines(keepends=True)


def regzip(lines: list[bytes]) -> bytes:
    return gzip.compress(b"".join(lines), mtime=0)


class TestARoundTrip:
    def test_an_artifact_reads_back_as_the_entries_written(self) -> None:
        data = write_place_index(header(), RECORDS)
        artifact = read_place_index(data)

        assert artifact.entry_count == 3
        assert artifact.gazetteer_version == 2
        assert artifact.file_sha256 == EXTRACT
        assert artifact.sha256 == hashlib.sha256(data).hexdigest()
        first = artifact.entries[0]
        assert first.kind is PlaceKind.PLACE
        assert first.label == "William G. Davis Computer Research Centre"
        assert first.codes == ("dc",)
        assert first.longitude == -80.5419973

    def test_the_same_index_always_writes_the_same_bytes(self) -> None:
        assert write_place_index(header(), RECORDS) == write_place_index(header(), RECORDS)

    def test_the_header_says_what_the_file_is(self) -> None:
        first = json.loads(lines_of(write_place_index(header(), RECORDS))[0])

        assert first["format"] == ARTIFACT_FORMAT
        assert first["entry_count"] == 3


class TestARefusedArtifact:
    def test_a_file_cut_short_is_refused(self) -> None:
        data = write_place_index(header(), RECORDS)

        with pytest.raises(PlaceIndexTransferError, match="not a readable"):
            read_place_index(data[:-40])

    def test_an_edited_entry_is_refused(self) -> None:
        lines = lines_of(write_place_index(header(), RECORDS))
        lines[2] = lines[2].replace(b"200 University", b"201 University")

        with pytest.raises(PlaceIndexTransferError, match="truncated or edited"):
            read_place_index(regzip(lines))

    def test_a_dropped_entry_is_refused_even_with_a_fresh_digest(self) -> None:
        lines = lines_of(write_place_index(header(), RECORDS))
        first = json.loads(lines[0])
        body = b"".join(lines[1:3])
        first["entries_sha256"] = hashlib.sha256(body).hexdigest()

        with pytest.raises(PlaceIndexTransferError, match="says 3 entries"):
            read_place_index(regzip([json.dumps(first).encode() + b"\n", body]))

    def test_counts_that_do_not_match_the_entries_are_refused(self) -> None:
        data = write_place_index(header(counts={"place": 3, "address": 0, "street": 0}), RECORDS)

        with pytest.raises(PlaceIndexTransferError, match="counts"):
            read_place_index(data)

    def test_another_kind_of_file_is_refused(self) -> None:
        with pytest.raises(PlaceIndexTransferError, match=ARTIFACT_FORMAT):
            read_place_index(gzip.compress(b'{"format": "something-else"}\n'))

    def test_a_newer_layout_is_refused(self) -> None:
        lines = lines_of(write_place_index(header(), RECORDS))
        first = json.loads(lines[0])
        first["format_version"] = 99

        with pytest.raises(PlaceIndexTransferError, match="layout 99"):
            read_place_index(regzip([json.dumps(first).encode() + b"\n", *lines[1:]]))
