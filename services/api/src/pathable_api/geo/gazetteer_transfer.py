"""Moving a built place index from one database to another, whole.

The place index is read from the 970 MB OpenStreetMap extract the network came
from. The public pilot does not keep that extract, and its host is too small to
read it beside a running API, so an index is built where the extract is and
carried to the host as a small artifact: the build's provenance and every
entry, as gzip-compressed JSON lines.

Three rules make the move safe:

* **The artifact proves its own content.** Its header states the gazetteer
  version that built it, the extract's SHA-256, the counts, and a SHA-256 over
  every entry line. A truncated or edited file is refused before anything is
  written.
* **It must describe the network it will serve beside.** An artifact read from
  any extract other than the one the active network was built from is refused:
  search and routing describe the same map, of the same date.
* **The swap is one transaction.** The old index is deleted and the new one
  written and counted in a single transaction, so a failure leaves the old
  index serving, and a search sees one index or the other, never half of one.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, text

from pathable_api.geo.datasets import get_active_dataset, require_region
from pathable_api.geo.enums import PlaceKind
from pathable_api.geo.gazetteer import GazetteerImport, PlaceEntry
from pathable_api.geo.gazetteer_store import gazetteer_build, replace_gazetteer, stored_version
from pathable_api.geo.models import GazetteerBuild, GazetteerEntry
from pathable_api.geo.place_text import GAZETTEER_VERSION

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from pathable_api.geo.models import PilotRegion

#: What the first line of an artifact says it is.
ARTIFACT_FORMAT = "pathable-place-index"
#: Bumped if the artifact's own layout changes, independently of the index's.
ARTIFACT_FORMAT_VERSION = 1

_ENTRY_FIELDS = (
    "kind",
    "label",
    "category",
    "search_text",
    "longitude",
    "latitude",
    "osm_type",
    "osm_id",
    "osm_version",
    "codes",
    "initialisms",
)


class PlaceIndexTransferError(ValueError):
    """An artifact that cannot be installed, and why. Nothing has been written."""


def _entry_line(record: dict[str, Any]) -> bytes:
    return (
        json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        + b"\n"
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


async def export_place_index(session: AsyncSession, region_slug: str) -> bytes:
    """A region's place index as an artifact: provenance, then every entry.

    Deterministic: the same index exports to the same bytes. Two builds from the
    same extract differ in when they were built, so they are compared by the
    header's ``entries_sha256``, which covers the entries alone.
    """
    build = await gazetteer_build(session, region_slug)
    if build is None:
        msg = f"{region_slug} has no place index to export."
        raise PlaceIndexTransferError(msg)

    rows = await session.execute(
        text(
            "SELECT kind, label, category, search_text, ST_X(geometry) AS longitude, "
            "ST_Y(geometry) AS latitude, osm_type, osm_id, osm_version, codes, initialisms "
            "FROM gazetteer_entries WHERE build_id = :build ORDER BY id"
        ),
        {"build": build.id},
    )
    records = [
        {
            **{name: getattr(row, name) for name in _ENTRY_FIELDS},
            "codes": list(row.codes),
            "initialisms": list(row.initialisms),
        }
        for row in rows
    ]
    return write_place_index(
        {
            "gazetteer_version": stored_version(build),
            "region": region_slug,
            "source_name": build.source_name,
            "file_name": build.file_name,
            "file_sha256": build.file_sha256,
            "source_timestamp": (
                build.source_timestamp.isoformat() if build.source_timestamp else None
            ),
            "built_at": build.built_at.isoformat(),
            "configuration": build.configuration,
            "counts": {
                PlaceKind.PLACE.value: build.place_count,
                PlaceKind.ADDRESS.value: build.address_count,
                PlaceKind.STREET.value: build.street_count,
            },
        },
        records,
    )


def write_place_index(header: dict[str, Any], records: list[dict[str, Any]]) -> bytes:
    """Serialise a header and its entries; the format, count and digest are added here."""
    lines = [_entry_line(record) for record in records]
    body = b"".join(lines)
    full_header = {
        "format": ARTIFACT_FORMAT,
        "format_version": ARTIFACT_FORMAT_VERSION,
        **header,
        "entry_count": len(lines),
        "entries_sha256": _sha256(body),
    }
    buffer = io.BytesIO()
    # mtime 0 and no file name in the gzip header, so the bytes depend only on
    # the content.
    with gzip.GzipFile(fileobj=buffer, mode="wb", filename="", mtime=0) as compressed:
        compressed.write(_entry_line(full_header))
        compressed.write(body)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Read and check
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlaceIndexArtifact:
    """An artifact that has proved its own content. Not yet checked against a database."""

    header: dict[str, Any]
    entries: list[PlaceEntry]
    #: SHA-256 of the artifact file itself, as read.
    sha256: str

    @property
    def gazetteer_version(self) -> int | None:
        version = self.header.get("gazetteer_version")
        return version if isinstance(version, int) else None

    @property
    def region(self) -> str:
        return str(self.header["region"])

    @property
    def file_sha256(self) -> str:
        return str(self.header["file_sha256"])

    @property
    def entry_count(self) -> int:
        return int(self.header["entry_count"])


def read_place_index(data: bytes) -> PlaceIndexArtifact:
    """Decompress and verify an artifact. Raises before anything could be written."""
    try:
        raw = gzip.decompress(data)
    except (OSError, EOFError) as error:
        msg = f"not a readable place-index artifact: {error}"
        raise PlaceIndexTransferError(msg) from error

    header_line, _, body = raw.partition(b"\n")
    try:
        header = json.loads(header_line)
    except json.JSONDecodeError as error:
        msg = "the artifact's first line is not its header"
        raise PlaceIndexTransferError(msg) from error
    if not isinstance(header, dict) or header.get("format") != ARTIFACT_FORMAT:
        msg = f"not a {ARTIFACT_FORMAT} artifact"
        raise PlaceIndexTransferError(msg)
    if header.get("format_version") != ARTIFACT_FORMAT_VERSION:
        msg = (
            f"artifact layout {header.get('format_version')!r}; this release reads "
            f"{ARTIFACT_FORMAT_VERSION}"
        )
        raise PlaceIndexTransferError(msg)
    missing = [
        key
        for key in ("region", "file_sha256", "entry_count", "entries_sha256", "counts")
        if key not in header
    ]
    if missing:
        msg = f"the artifact's header lacks {', '.join(missing)}"
        raise PlaceIndexTransferError(msg)

    if _sha256(body) != header["entries_sha256"]:
        msg = "the entries do not match the SHA-256 in the header: truncated or edited"
        raise PlaceIndexTransferError(msg)

    entries: list[PlaceEntry] = []
    for number, line in enumerate(body.splitlines(), start=2):
        try:
            record = json.loads(line)
            entries.append(
                PlaceEntry(
                    kind=PlaceKind(record["kind"]),
                    label=record["label"],
                    category=record["category"],
                    search_text=record["search_text"],
                    longitude=float(record["longitude"]),
                    latitude=float(record["latitude"]),
                    osm_type=record["osm_type"],
                    osm_id=int(record["osm_id"]),
                    osm_version=record["osm_version"],
                    codes=tuple(record["codes"]),
                    initialisms=tuple(record["initialisms"]),
                )
            )
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as error:
            msg = f"line {number} is not a place-index entry: {error}"
            raise PlaceIndexTransferError(msg) from error

    if len(entries) != header["entry_count"]:
        msg = f"the header says {header['entry_count']} entries; the artifact holds {len(entries)}"
        raise PlaceIndexTransferError(msg)
    counted = {kind.value: sum(1 for entry in entries if entry.kind is kind) for kind in PlaceKind}
    if counted != {kind.value: int(header["counts"].get(kind.value, -1)) for kind in PlaceKind}:
        msg = f"the header's counts {header['counts']} are not the entries' {counted}"
        raise PlaceIndexTransferError(msg)

    return PlaceIndexArtifact(header=header, entries=entries, sha256=_sha256(data))


async def check_place_index(
    session: AsyncSession, artifact: PlaceIndexArtifact, *, region_slug: str
) -> PilotRegion:
    """Whether this release, and this database's network, can take the artifact.

    Reads only, and needs no migration: it can run before a deployment
    switches, against the schema the previous release left.
    """
    if artifact.region != region_slug:
        msg = f"the artifact is {artifact.region}'s place index, not {region_slug}'s"
        raise PlaceIndexTransferError(msg)
    if artifact.gazetteer_version != GAZETTEER_VERSION:
        msg = (
            f"the artifact is place index version {artifact.gazetteer_version}; this "
            f"release reads version {GAZETTEER_VERSION}"
        )
        raise PlaceIndexTransferError(msg)

    region = await require_region(session, region_slug)
    active = await get_active_dataset(session, region.id)
    network = active.ingestion_configuration.get("file_sha256") if active else None
    if network is None:
        msg = f"{region_slug} has no active network recording the extract it came from"
        raise PlaceIndexTransferError(msg)
    if network != artifact.file_sha256:
        msg = (
            f"the artifact was read from extract {artifact.file_sha256[:16]}…, but the active "
            f"network came from {network[:16]}…; search and routing must describe the same map"
        )
        raise PlaceIndexTransferError(msg)
    return region


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


async def install_place_index(
    session: AsyncSession, artifact: PlaceIndexArtifact, *, region_slug: str
) -> GazetteerBuild:
    """Replace a region's place index with the artifact's, in the caller's transaction.

    Checks first, then deletes the old index, writes the new one, and counts it
    back. The caller commits; on any error it rolls back, and the old index is
    still the one serving.
    """
    region = await check_place_index(session, artifact, region_slug=region_slug)
    has_codes = await session.scalar(
        text(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = "
            "'gazetteer_entries' AND column_name IN ('codes', 'initialisms')"
        )
    )
    if has_codes != 2:
        msg = (
            "the database has no codes or initialisms columns: migrate it to this release's "
            "head (0008_place_codes) first"
        )
        raise PlaceIndexTransferError(msg)

    header = artifact.header
    stamp = header.get("source_timestamp")
    configuration = {
        **header.get("configuration", {}),
        "installed_from": {
            "artifact_sha256": artifact.sha256,
            "built_at": header.get("built_at"),
        },
    }
    build = await replace_gazetteer(
        session,
        region=region,
        source_name=str(header.get("source_name") or f"openstreetmap-pbf:{region_slug}"),
        extract=GazetteerImport(
            entries=artifact.entries,
            file_name=str(header.get("file_name", "")),
            file_sha256=artifact.file_sha256,
            source_timestamp=dt.datetime.fromisoformat(stamp) if stamp else None,
            configuration=configuration,
        ),
    )
    written = await session.scalar(
        select(func.count(GazetteerEntry.id)).where(GazetteerEntry.build_id == build.id)
    )
    if written != artifact.entry_count:
        msg = f"wrote {written} entries where the artifact holds {artifact.entry_count}"
        raise PlaceIndexTransferError(msg)
    return build


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PlaceIndexStatus:
    """Whether a region's stored place index is the one this release expects."""

    region: str
    present: bool
    stored_version: int | None
    expected_version: int
    file_sha256: str | None
    network_sha256: str | None
    entries: int
    problems: list[str] = field(default_factory=list)

    @property
    def current(self) -> bool:
        return self.present and not self.problems


async def place_index_status(session: AsyncSession, region_slug: str) -> PlaceIndexStatus:
    """Compare the stored index with this release and with the active network.

    Reads the build record only, which every schema since 0007 has, so the
    release about to be deployed can ask it of the database the previous one
    left, before anything is migrated.
    """
    region = await require_region(session, region_slug)
    build = await gazetteer_build(session, region_slug)
    active = await get_active_dataset(session, region.id)
    network = active.ingestion_configuration.get("file_sha256") if active else None
    if build is None:
        return PlaceIndexStatus(
            region=region_slug,
            present=False,
            stored_version=None,
            expected_version=GAZETTEER_VERSION,
            file_sha256=None,
            network_sha256=network,
            entries=0,
            problems=["there is no place index"],
        )

    version = stored_version(build)
    problems: list[str] = []
    if version != GAZETTEER_VERSION:
        problems.append(
            f"the place index is version {version}; this release expects {GAZETTEER_VERSION}"
        )
    if network is None:
        problems.append("no active network records the extract it came from")
    elif network != build.file_sha256:
        problems.append(
            f"the place index came from extract {build.file_sha256[:16]}…, the active network "
            f"from {network[:16]}…"
        )
    return PlaceIndexStatus(
        region=region_slug,
        present=True,
        stored_version=version,
        expected_version=GAZETTEER_VERSION,
        file_sha256=build.file_sha256,
        network_sha256=network,
        entries=build.place_count + build.address_count + build.street_count,
        problems=problems,
    )


__all__ = [
    "ARTIFACT_FORMAT",
    "ARTIFACT_FORMAT_VERSION",
    "PlaceIndexArtifact",
    "PlaceIndexStatus",
    "PlaceIndexTransferError",
    "check_place_index",
    "export_place_index",
    "install_place_index",
    "place_index_status",
    "read_place_index",
    "write_place_index",
]
