"""Moving a place index between databases, against real PostGIS.

The public pilot's place index is data restored rather than rebuilt, so a
release that reads a newer gazetteer version needs the index carried to it. The
path this file proves is the production one: a database at 0007 holding a
version 1 index, asked by the new release's code before anything migrates,
then migrated, then given the version 2 index in one transaction.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from pathable_api.cli import main
from pathable_api.core.config import get_settings
from pathable_api.geo.datasets import ingest_network
from pathable_api.geo.enums import SourceType
from pathable_api.geo.fixtures import build_synthetic_network
from pathable_api.geo.gazetteer import GazetteerImport, PlaceEntry
from pathable_api.geo.gazetteer_store import gazetteer_build, replace_gazetteer, search_gazetteer
from pathable_api.geo.gazetteer_transfer import (
    PlaceIndexTransferError,
    check_place_index,
    export_place_index,
    install_place_index,
    place_index_status,
    read_place_index,
    write_place_index,
)
from pathable_api.geo.models import GazetteerBuild, GazetteerEntry
from pathable_api.geo.regions import WATERLOO, seed_region
from pathable_api.main import create_app
from pathable_api.routing.activation import promote
from tests.conftest import build_settings
from tests.integration.conftest import alembic_config
from tests.integration.test_place_search import AS_OF, CODED_ENTRIES

pytestmark = pytest.mark.integration

EXTRACT = "c" * 64
OTHER_EXTRACT = "d" * 64
READY_URL = "/api/v1/health/ready"


async def active_network(session: AsyncSession, *, extract: str = EXTRACT) -> None:
    """An active Waterloo network recording the extract it was read from.

    Invented, and activated through the real gates: seal, route regression,
    an acceptance that says what it is.
    """
    region = await seed_region(session, WATERLOO)
    result = await ingest_network(
        session,
        region=region,
        payload=build_synthetic_network(),
        source_type=SourceType.SYNTHETIC,
        source_name="place-index-transfer-test",
        ingestion_configuration={"file_sha256": extract},
        elevation_required=False,
    )
    await promote(
        session,
        result.dataset_id,
        acceptance_reason="Invented network for the place-index transfer tests.",
    )
    await session.commit()


async def stored_index(
    session: AsyncSession, *, version: int = 2, extract: str = EXTRACT
) -> GazetteerBuild:
    region = await seed_region(session, WATERLOO)
    build = await replace_gazetteer(
        session,
        region=region,
        source_name="openstreetmap-pbf:waterloo",
        extract=GazetteerImport(
            entries=CODED_ENTRIES,
            file_name="ontario-latest.osm.pbf",
            file_sha256=extract,
            source_timestamp=AS_OF,
            configuration={"source": "openstreetmap", "gazetteer_version": version},
        ),
    )
    await session.commit()
    return build


def record_of(entry: PlaceEntry, **changes: Any) -> dict[str, Any]:
    return {
        "kind": entry.kind.value,
        "label": entry.label,
        "category": entry.category,
        "search_text": entry.search_text,
        "longitude": entry.longitude,
        "latitude": entry.latitude,
        "osm_type": entry.osm_type,
        "osm_id": entry.osm_id,
        "osm_version": entry.osm_version,
        "codes": list(entry.codes),
        "initialisms": list(entry.initialisms),
        **changes,
    }


def artifact_bytes(
    *,
    version: int = 2,
    extract: str = EXTRACT,
    records: list[dict[str, Any]] | None = None,
) -> bytes:
    records = records if records is not None else [record_of(e) for e in CODED_ENTRIES]
    counts = {"place": 0, "address": 0, "street": 0}
    for item in records:
        counts[item["kind"]] += 1
    return write_place_index(
        {
            "gazetteer_version": version,
            "region": "waterloo",
            "source_name": "openstreetmap-pbf:waterloo",
            "file_name": "ontario-latest.osm.pbf",
            "file_sha256": extract,
            "source_timestamp": AS_OF.isoformat(),
            "built_at": dt.datetime(2026, 11, 2, tzinfo=dt.UTC).isoformat(),
            "configuration": {"source": "openstreetmap", "gazetteer_version": version},
            "counts": counts,
        },
        records,
    )


async def first_match(session: AsyncSession, query: str) -> tuple[str, str | None]:
    build = await gazetteer_build(session, "waterloo")
    assert build is not None
    [first, *_] = await search_gazetteer(session, build.id, query, limit=5)
    return first.label, first.matched_on


async def entry_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count(GazetteerEntry.id))) or 0)


class TestMovingAnIndex:
    async def test_an_exported_index_installs_as_the_same_index(
        self, db_session: AsyncSession
    ) -> None:
        await active_network(db_session)
        original = await stored_index(db_session)
        before = await first_match(db_session, "DC")

        artifact = read_place_index(await export_place_index(db_session, "waterloo"))
        installed = await install_place_index(db_session, artifact, region_slug="waterloo")
        await db_session.commit()

        assert installed.id != original.id
        assert await db_session.scalar(select(func.count(GazetteerBuild.id))) == 1
        assert (
            await first_match(db_session, "DC")
            == before
            == (
                "William G. Davis Computer Research Centre",
                "code",
            )
        )
        again = read_place_index(await export_place_index(db_session, "waterloo"))
        assert again.header["entries_sha256"] == artifact.header["entries_sha256"]
        assert installed.configuration["installed_from"]["artifact_sha256"] == artifact.sha256

    async def test_an_index_from_another_extract_is_refused_and_nothing_changes(
        self, db_session: AsyncSession
    ) -> None:
        # Search and routing must describe the same map, of the same date.
        await active_network(db_session)
        original = await stored_index(db_session)
        artifact = read_place_index(artifact_bytes(extract=OTHER_EXTRACT))

        with pytest.raises(PlaceIndexTransferError, match="same map"):
            await install_place_index(db_session, artifact, region_slug="waterloo")
        await db_session.rollback()

        kept = await gazetteer_build(db_session, "waterloo")
        assert kept is not None
        assert kept.id == original.id

    async def test_an_index_of_another_version_is_refused(self, db_session: AsyncSession) -> None:
        await active_network(db_session)
        await stored_index(db_session)

        with pytest.raises(PlaceIndexTransferError, match="reads version 2"):
            await check_place_index(
                db_session, read_place_index(artifact_bytes(version=1)), region_slug="waterloo"
            )

    async def test_a_failure_part_way_through_leaves_the_old_index_serving(
        self, db_session: AsyncSession
    ) -> None:
        # The last entry breaks a database constraint, after the old index has
        # been deleted and every other entry written. All of it rolls back.
        await active_network(db_session)
        original = await stored_index(db_session)
        records = [record_of(entry) for entry in CODED_ENTRIES]
        records[-1] = {**records[-1], "osm_type": "polygon"}
        artifact = read_place_index(artifact_bytes(records=records))

        with pytest.raises(DBAPIError):
            await install_place_index(db_session, artifact, region_slug="waterloo")
        await db_session.rollback()

        kept = await gazetteer_build(db_session, "waterloo")
        assert kept is not None
        assert kept.id == original.id
        assert await entry_count(db_session) == len(CODED_ENTRIES)
        assert (await first_match(db_session, "DC"))[1] == "code"


async def version_one_index(session: AsyncSession) -> None:
    """A version 1 index as a 0007 database holds it: no codes, no initials."""
    region = await seed_region(session, WATERLOO)
    build = GazetteerBuild(
        pilot_region_id=region.id,
        source_name="openstreetmap-pbf:waterloo",
        file_name="ontario-latest.osm.pbf",
        file_sha256=EXTRACT,
        source_timestamp=AS_OF,
        place_count=len(CODED_ENTRIES),
        address_count=0,
        street_count=0,
        configuration={"source": "openstreetmap", "gazetteer_version": 1},
    )
    session.add(build)
    await session.flush()
    for entry in CODED_ENTRIES:
        await session.execute(
            text(
                "INSERT INTO gazetteer_entries (build_id, kind, label, category, search_text, "
                "geometry, osm_type, osm_id) VALUES (:build, 'place', :label, :category, "
                ":search_text, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 'way', :osm_id)"
            ),
            {
                "build": build.id,
                "label": entry.label,
                "category": entry.category,
                "search_text": entry.search_text,
                "lon": entry.longitude,
                "lat": entry.latitude,
                "osm_id": entry.osm_id,
            },
        )
    await session.commit()


class TestTheUpgradeFromVersionOne:
    async def test_a_version_one_index_at_0007_is_detected_then_replaced(
        self, migrated_database_url: str, db_session: AsyncSession
    ) -> None:
        # The production path, in order: the database the previous release
        # left, the new release asking before it migrates, the migration, the
        # one-transaction swap.
        command.downgrade(alembic_config(migrated_database_url), "0007_place_search")
        await active_network(db_session)
        await version_one_index(db_session)

        status = await place_index_status(db_session, "waterloo")
        assert status.present
        assert status.stored_version == 1
        assert not status.current
        assert "version 1" in status.problems[0]

        artifact = read_place_index(artifact_bytes())
        await check_place_index(db_session, artifact, region_slug="waterloo")
        with pytest.raises(PlaceIndexTransferError, match="0008_place_codes"):
            await install_place_index(db_session, artifact, region_slug="waterloo")
        await db_session.rollback()

        command.upgrade(alembic_config(migrated_database_url), "head")
        await install_place_index(db_session, artifact, region_slug="waterloo")
        await db_session.commit()

        assert (await place_index_status(db_session, "waterloo")).current
        assert await first_match(db_session, "DC") == (
            "William G. Davis Computer Research Centre",
            "code",
        )
        assert await entry_count(db_session) == len(CODED_ENTRIES)

    async def test_no_index_is_not_current(self, db_session: AsyncSession) -> None:
        await active_network(db_session)

        status = await place_index_status(db_session, "waterloo")

        assert not status.present
        assert not status.current

    async def test_an_index_from_another_extract_is_not_current(
        self, db_session: AsyncSession
    ) -> None:
        await active_network(db_session)
        await stored_index(db_session, extract=OTHER_EXTRACT)

        status = await place_index_status(db_session, "waterloo")

        assert status.stored_version == 2
        assert not status.current
        assert "active network" in status.problems[0]


# --- Through the command line and the readiness report ---------------------------

Cli = Callable[[list[str]], int]


def run(database_url: str, setup: Callable[[AsyncSession], Any]) -> None:
    """Prepare a database from a synchronous test, as the command line needs."""

    async def go() -> None:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                await setup(session)
        finally:
            await engine.dispose()

    asyncio.run(go())


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch, migrated_database_url: str) -> Iterator[Cli]:
    monkeypatch.setenv("DATABASE_URL", migrated_database_url)
    get_settings.cache_clear()
    yield main
    get_settings.cache_clear()


class TestCommandLine:
    def test_status_import_and_export(
        self,
        cli: Cli,
        migrated_database_url: str,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        async def setup(session: AsyncSession) -> None:
            await active_network(session)
            await stored_index(session, version=1)

        run(migrated_database_url, setup)
        artifact = tmp_path / "waterloo-place-index-v2.jsonl.gz"
        artifact.write_bytes(artifact_bytes())

        assert cli(["gazetteer", "status", "--region", "waterloo"]) == 1
        assert "this release expects 2" in capsys.readouterr().out

        assert (
            cli(["gazetteer", "import", "--region", "waterloo", "--check", "--file", str(artifact)])
            == 0
        )
        assert "nothing was written" in capsys.readouterr().out
        assert cli(["gazetteer", "status", "--region", "waterloo"]) == 1

        assert cli(["gazetteer", "import", "--region", "waterloo", "--file", str(artifact)]) == 0
        assert "replaced in one transaction" in capsys.readouterr().out
        assert cli(["gazetteer", "status", "--region", "waterloo"]) == 0

        exported = tmp_path / "exported.jsonl.gz"
        assert cli(["gazetteer", "export", "--region", "waterloo", "--out", str(exported)]) == 0
        assert (
            read_place_index(exported.read_bytes()).header["entries_sha256"]
            == read_place_index(artifact.read_bytes()).header["entries_sha256"]
        )

    def test_an_artifact_that_does_not_fit_writes_nothing(
        self,
        cli: Cli,
        migrated_database_url: str,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        async def setup(session: AsyncSession) -> None:
            await active_network(session)
            await stored_index(session, version=1)

        run(migrated_database_url, setup)
        artifact = tmp_path / "other.jsonl.gz"
        artifact.write_bytes(artifact_bytes(extract=OTHER_EXTRACT))

        assert cli(["gazetteer", "import", "--region", "waterloo", "--file", str(artifact)]) == 1
        assert "Nothing was written" in capsys.readouterr().err
        assert cli(["gazetteer", "status", "--region", "waterloo"]) == 1


def readiness(database_url: str) -> dict[str, Any]:
    settings = build_settings(database_url=database_url, geocoding_provider="local")
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.get(READY_URL)
    body: dict[str, Any] = response.json()
    body["http_status"] = response.status_code
    return body


@pytest_asyncio.fixture
async def stale_database_url(
    migrated_database_url: str, db_session: AsyncSession
) -> AsyncIterator[str]:
    await stored_index(db_session, version=1)
    yield migrated_database_url


@pytest_asyncio.fixture
async def current_database_url(
    migrated_database_url: str, db_session: AsyncSession
) -> AsyncIterator[str]:
    await stored_index(db_session, version=2)
    yield migrated_database_url


class TestReadiness:
    def test_a_stale_index_is_reported_and_readiness_is_unaffected(
        self, stale_database_url: str
    ) -> None:
        # Search is degraded, routing is not: readiness stays ready, and says why
        # search is not this release's.
        body = readiness(stale_database_url)

        assert body["http_status"] == 200
        assert body["status"] == "ready"
        assert body["place_index"]["status"] == "stale"
        assert body["place_index"]["stored_version"] == 1
        assert body["place_index"]["expected_version"] == 2
        assert "waterloo: version 1" in body["place_index"]["detail"]

    def test_a_current_index_is_reported_current(self, current_database_url: str) -> None:
        body = readiness(current_database_url)

        assert body["place_index"]["status"] == "current"
        assert body["place_index"]["stored_version"] == 2

    def test_no_index_is_reported_absent(self, migrated_database_url: str) -> None:
        assert readiness(migrated_database_url)["place_index"]["status"] == "absent"

    def test_search_that_is_not_local_does_not_use_the_index(
        self, migrated_database_url: str
    ) -> None:
        settings = build_settings(database_url=migrated_database_url, geocoding_provider="none")
        with TestClient(create_app(settings)) as client:
            body = client.get(READY_URL).json()

        assert body["place_index"]["status"] == "not_used"
