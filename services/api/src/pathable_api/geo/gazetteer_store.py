"""Storing a region's place index and searching it.

Matching is deliberately plain. Every word typed must start a word in the entry
(a house number must match whole, so "200" never finds "1200"); results rank a
short code OpenStreetMap records for the place (DC, SLC, E5), then an exact
name, then a recorded code the query begins ("sl" while typing SLC), then the
generated initials of a name — a recorded code outranks a generated one, since
two-letter initials are shared by half the city — then an entry that starts
with the query, then places before streets before addresses — or addresses first when
the query starts with a number — then trigram similarity. Features named after the place they serve
(a taxi stand, an information board, a stop) rank after that place; see
:func:`~pathable_api.geo.place_text.category_tier`. Only when nothing matches
does a typo-tolerant pass run, on ``pg_trgm`` word similarity, so
"Konestoga Mall" still finds the mall without letting fuzzy matches crowd out
exact ones.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from geoalchemy2.elements import WKTElement
from sqlalchemy import delete, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pathable_api.geo.enums import PlaceKind
from pathable_api.geo.models import SRID, GazetteerBuild, GazetteerEntry, PilotRegion
from pathable_api.geo.place_text import (
    INCIDENTAL_CATEGORIES,
    TRANSIT_CATEGORIES,
    compact_query,
    query_terms,
)

if TYPE_CHECKING:
    # The extract reader imports osmium; the API only ever searches.
    from pathable_api.geo.gazetteer import GazetteerImport

_INSERT_CHUNK = 5_000


async def replace_gazetteer(
    session: AsyncSession,
    *,
    region: PilotRegion,
    source_name: str,
    extract: GazetteerImport,
) -> GazetteerBuild:
    """Swap a region's place index for a new one.

    The old index is deleted and the new one written in the caller's
    transaction, so a concurrent search sees one index or the other — never a
    half-written one, and never both.
    """
    await session.execute(delete(GazetteerBuild).where(GazetteerBuild.pilot_region_id == region.id))

    build = GazetteerBuild(
        pilot_region_id=region.id,
        source_name=source_name,
        file_name=extract.file_name,
        file_sha256=extract.file_sha256,
        source_timestamp=extract.source_timestamp,
        place_count=extract.count(PlaceKind.PLACE),
        address_count=extract.count(PlaceKind.ADDRESS),
        street_count=extract.count(PlaceKind.STREET),
        configuration=extract.configuration,
    )
    session.add(build)
    await session.flush()

    rows: list[dict[str, Any]] = [
        {
            "build_id": build.id,
            "kind": entry.kind.value,
            "label": entry.label,
            "category": entry.category,
            "search_text": entry.search_text,
            "geometry": WKTElement(f"POINT({entry.longitude} {entry.latitude})", srid=SRID),
            "osm_type": entry.osm_type,
            "osm_id": entry.osm_id,
            "osm_version": entry.osm_version,
            "codes": list(entry.codes),
            "initialisms": list(entry.initialisms),
        }
        for entry in extract.entries
        if entry.search_text
    ]
    for start in range(0, len(rows), _INSERT_CHUNK):
        await session.execute(insert(GazetteerEntry), rows[start : start + _INSERT_CHUNK])
    return build


async def gazetteer_build(session: AsyncSession, region_slug: str) -> GazetteerBuild | None:
    """The place index serving a region, or None when none has been built."""
    result = await session.execute(
        select(GazetteerBuild)
        .join(PilotRegion, PilotRegion.id == GazetteerBuild.pilot_region_id)
        .where(PilotRegion.slug == region_slug)
    )
    return result.scalar_one_or_none()


def stored_version(build: GazetteerBuild) -> int | None:
    """The gazetteer version that built a stored index, as its build recorded it."""
    version = build.configuration.get("gazetteer_version")
    return version if isinstance(version, int) else None


async def place_index_versions(session: AsyncSession) -> list[tuple[str, int | None]]:
    """Each region with a place index, and the gazetteer version that built it.

    Reads the build records only, so it costs one small query and works on any
    schema since 0007.
    """
    rows = await session.execute(
        select(PilotRegion.slug, GazetteerBuild.configuration)
        .join(PilotRegion, PilotRegion.id == GazetteerBuild.pilot_region_id)
        .order_by(PilotRegion.slug)
    )
    return [
        (
            slug,
            version if isinstance(version := configuration.get("gazetteer_version"), int) else None,
        )
        for slug, configuration in rows
    ]


#: How a result matched: a recorded code, generated initials, its name or
#: address, or the typo-tolerant pass.
MatchedOn = Literal["code", "initialism", "name", "fuzzy"]


@dataclass(frozen=True, slots=True)
class GazetteerMatch:
    label: str
    category: str | None
    kind: PlaceKind
    longitude: float
    latitude: float
    matched_on: MatchedOn = "name"
    #: The OpenStreetMap element the entry was read from.
    osm_type: str = ""
    osm_id: int = 0


#: A recorded code the query is the start of: "sl" offers SLC while it is typed.
_CODE_PREFIX = (
    "char_length(CAST(:code AS text)) >= 2 AND EXISTS "
    "(SELECT 1 FROM unnest(codes) AS recorded(code) WHERE recorded.code LIKE :code_prefix)"
)

_CANDIDATES = """
SELECT label, category, kind, longitude, latitude, matched_on, osm_type, osm_id FROM (
  SELECT
    id, label, category, kind, search_text, osm_type, osm_id,
    ST_X(geometry) AS longitude,
    ST_Y(geometry) AS latitude,
    CASE
      WHEN category = ANY (CAST(:incidental AS text[])) THEN 2
      WHEN category = ANY (CAST(:transit AS text[])) THEN 1
      ELSE 0
    END AS tier,
    CASE
      WHEN CAST(:code AS text) = ANY (codes) THEN 'code'
      WHEN {code_prefix} THEN 'code'
      WHEN CAST(:code AS text) = ANY (initialisms) THEN 'initialism'
      ELSE {matched}
    END AS matched_on,
    CAST(:code AS text) = ANY (codes) AS exact_code
  FROM gazetteer_entries
  WHERE build_id = :build_id AND {condition}
) AS matched
"""

_KIND = (
    "CASE kind WHEN 'place' THEN :place_rank WHEN 'street' THEN :street_rank ELSE :address_rank END"
)

# An exact or prefix match lifts an entry only when it is something a person is
# likely going to, or a stop serving it — never a taxi stand that shares the name.
_EXACT = (
    _CANDIDATES.format(
        code_prefix=_CODE_PREFIX,
        matched="'name'",
        condition=(
            "(search_text ~ ALL (CAST(:patterns AS text[])) "
            "OR CAST(:code AS text) = ANY (codes) "
            "OR CAST(:code AS text) = ANY (initialisms) "
            f"OR ({_CODE_PREFIX}))"
        ),
    )
    + f"""
ORDER BY
  CASE
    WHEN exact_code THEN 0
    WHEN tier = 0 AND search_text = :query THEN 1
    WHEN tier = 0 AND matched_on = 'code' THEN 2
    WHEN tier = 0 AND matched_on = 'initialism' THEN 3
    WHEN tier = 0 AND search_text LIKE :prefix THEN 4
    WHEN tier = 1 AND search_text = :query THEN 5
    WHEN tier = 1 AND search_text LIKE :prefix THEN 6
    ELSE 7
  END,
  {_KIND},
  tier,
  similarity(search_text, :query) DESC,
  char_length(search_text),
  id
LIMIT :limit
"""
)

_FUZZY = (
    _CANDIDATES.format(
        code_prefix=_CODE_PREFIX, matched="'fuzzy'", condition=":query <% search_text"
    )
    + f"""
ORDER BY word_similarity(:query, search_text) DESC, {_KIND}, tier, char_length(search_text), id
LIMIT :limit
"""
)

#: Rows asked of the database beyond the limit, so results that repeat one
#: another can be dropped and the limit still filled.
_SPARE_ROWS = 5


def match_patterns(terms: tuple[str, ...]) -> list[str]:
    """One POSIX regex per term: a word that starts with it, or a whole number.

    Terms come from :func:`~pathable_api.geo.gazetteer.query_terms`, so they hold
    only ``[a-z0-9]`` and nothing in them needs escaping.
    """
    return [rf"\m{term}\M" if term.isdigit() else rf"\m{term}" for term in terms]


def _ranks(terms: tuple[str, ...]) -> dict[str, int]:
    if terms and terms[0][0].isdigit():
        return {"address_rank": 0, "place_rank": 1, "street_rank": 2}
    return {"place_rank": 0, "street_rank": 1, "address_rank": 2}


async def search_gazetteer(
    session: AsyncSession,
    build_id: uuid.UUID,
    query: str,
    *,
    limit: int,
) -> list[GazetteerMatch]:
    """Up to ``limit`` entries for ``query`` in one place index."""
    terms = query_terms(query)
    if not terms:
        return []
    normalised = " ".join(terms)
    code = compact_query(query)
    parameters: dict[str, Any] = {
        "build_id": build_id,
        "query": normalised,
        "code": code,
        "code_prefix": f"{code}%",
        "limit": limit + _SPARE_ROWS,
        "incidental": sorted(INCIDENTAL_CATEGORIES),
        "transit": sorted(TRANSIT_CATEGORIES),
        **_ranks(terms),
    }
    exact = {**parameters, "prefix": f"{normalised}%", "patterns": match_patterns(terms)}
    rows = (await session.execute(text(_EXACT), exact)).all()
    if not rows:
        rows = (await session.execute(text(_FUZZY), parameters)).all()

    # One result per thing a person would pick: the campus's address is
    # recorded on two of its elements, and listing it twice reads as a mistake.
    # A stop and an information board that share a square's name are different
    # things, so the category is part of what makes a result the same.
    seen: set[tuple[str, str, str | None]] = set()
    matches: list[GazetteerMatch] = []
    for row in rows:
        key = (row.kind, row.label, row.category)
        if key in seen:
            continue
        seen.add(key)
        matches.append(
            GazetteerMatch(
                label=row.label,
                category=row.category,
                kind=PlaceKind(row.kind),
                longitude=float(row.longitude),
                latitude=float(row.latitude),
                matched_on=row.matched_on,
                osm_type=row.osm_type,
                osm_id=int(row.osm_id),
            )
        )
    return matches[:limit]


def attribution(build: GazetteerBuild) -> str:
    """The credit ODbL requires, with the date the data is current to."""
    as_of = (
        build.source_timestamp.astimezone(dt.UTC).date().isoformat()
        if build.source_timestamp is not None
        else "an unrecorded date"
    )
    return f"Places from OpenStreetMap as of {as_of}. © OpenStreetMap contributors, ODbL 1.0"
