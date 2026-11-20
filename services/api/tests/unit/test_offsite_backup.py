"""A backup counts only once S3 provably holds it, and no credential leaks.

infra/vps/backup.sh and offsite.py are what let the pilot's database survive
losing its host. What they must never do: call a backup done when S3 does not
hold exactly its bytes; replace an object already in the bucket (it is
versioned with a 7-day expiry of old versions, so a replacement destroys the
earlier backup a week later); put the AWS keys on a command line or in a log;
or read the keys with echo on. Nor may deploy.sh or restore.sh change
production on a backup S3 does not hold, unless told to skip it. A fake `docker`
(fake_docker.py) plays the database container and S3 both, and records every
command it is sent.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import time
import urllib.parse
import warnings
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the tooling runs on Linux hosts")

REPO = Path(__file__).resolve().parents[4]
VPS = REPO / "infra" / "vps"
FAKE_DOCKER = Path(__file__).with_name("fake_docker.py")

# Built at run time so no key-shaped literal sits in the repository.
KEY_ID = "AKIA" + "Q" * 16
SECRET = "S" * 20 + "e" * 20
REVISION = "a1" * 20
DATASET = "51585450-8ff5-409d-a02e-66d5a3c5e260"
DATABASE: dict[str, Any] = {
    "alembic_revision": "0008_place_codes",
    "server_version": "17.5",
    "database_bytes": 203_423_744,
    "active_datasets": [
        {
            "id": DATASET,
            "source_type": "osm",
            "pilot_region_id": "68d3cc1a-2427-43c6-9a55-705f2ed98668",
            "checksum": "c" * 64,
            "content_checksum": "d" * 64,
            "content_checksum_version": 2,
            "node_count": 155_714,
            "edge_count": 180_554,
        }
    ],
    "place_index": {"entries": 33_225, "builds": 1},
    "table_rows": {
        "alembic_version": 1,
        "dataset_versions": 3,
        "gazetteer_entries": 33_225,
        "graph_edges": 180_580,
        "graph_nodes": 155_732,
    },
}


def _module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


offsite = _module("pathable_vps_offsite_under_test", VPS / "offsite.py")


@dataclass
class Host:
    """A fake /srv/pathable, backup directory and Docker, isolated per test."""

    root: Path
    backups: Path
    state: Path
    env: dict[str, str]

    def calls(self) -> list[list[str]]:
        log = self.state / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def aws_calls(self, operation: str | None = None) -> list[list[str]]:
        return [
            call
            for call in self.calls()
            if call[0] == "run"
            and any(arg.startswith("amazon/aws-cli:") for arg in call)
            and (operation is None or operation in call)
        ]

    def s3_objects(self) -> dict[str, dict[str, Any]]:
        s3 = self.state / "s3"
        if not s3.exists():
            return {}
        return {
            urllib.parse.unquote(meta.name[: -len(".meta.json")]): json.loads(meta.read_text())
            for meta in s3.glob("*.meta.json")
        }

    def s3_bytes(self, key: str) -> bytes:
        return (self.state / "s3" / urllib.parse.quote(key, safe="")).read_bytes()

    def dump(self, stamp: str = "20261117T040000Z", content: bytes | None = None) -> Path:
        """A dump file and its manifest, as backup.sh leaves them."""
        path = self.backups / f"pathable-{stamp}-scheduled-{REVISION[:12]}.dump"
        path.write_bytes(content if content is not None else b"PGDMP" + stamp.encode() * 300)
        code = offsite.main(
            [
                "manifest",
                str(path),
                "--label",
                "scheduled",
                "--started",
                "2026-11-17T04:00:00Z",
                "--dump-seconds",
                "5.2",
                "--container",
                "fake-db",
            ]
        )
        assert code == 0
        return path

    def faults(self, monkeypatch: pytest.MonkeyPatch, *faults: str) -> None:
        self.env["FAKE_S3_FAULTS"] = ",".join(faults)
        monkeypatch.setenv("FAKE_S3_FAULTS", ",".join(faults))


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Host:
    root = tmp_path / "srv"
    secrets = root / "secrets"
    secrets.mkdir(parents=True)
    secrets.chmod(0o700)
    backups = tmp_path / "backups"
    backups.mkdir()
    state = tmp_path / "docker"
    state.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, target in (
        ("docker", f'"{sys.executable}" "{FAKE_DOCKER}"'),
        ("python3", f'"{sys.executable}"'),
    ):
        shim = bin_dir / name
        shim.write_text(f'#!/bin/sh\nexec {target} "$@"\n')
        shim.chmod(0o755)

    (root / "offsite.env").write_text(
        "PATHABLE_OFFSITE_BUCKET=pathable-test-bucket\n"
        "PATHABLE_OFFSITE_REGION=ca-central-1\n"
        "PATHABLE_OFFSITE_PREFIX=backups/\n"
    )
    credentials = secrets / "aws-backup.credentials"
    credentials.write_text(
        f"[default]\naws_access_key_id = {KEY_ID}\naws_secret_access_key = {SECRET}\n"
    )
    credentials.chmod(0o600)
    (root / "release.env").write_text(
        f"PATHABLE_REVISION={REVISION}\n"
        "PATHABLE_APP_VERSION=0.1.0+a1a1a1a1a1a1\n"
        "PATHABLE_DEPLOYED_AT=2026-11-16T06:02:32Z\n"
    )
    (state / "db.json").write_text(json.dumps(DATABASE))
    (state / "dump.bin").write_bytes(b"PGDMP" + b"pathable" * 512)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "PATHABLE_ROOT": str(root),
        "PATHABLE_BACKUP_DIR": str(backups),
        "PATHABLE_DB_CONTAINER": "fake-db",
        "FAKE_DOCKER_STATE": str(state),
        "FAKE_S3_FAULTS": "",
    }
    for key in ("PATH", "PATHABLE_ROOT", "FAKE_DOCKER_STATE", "FAKE_S3_FAULTS"):
        monkeypatch.setenv(key, env[key])
    return Host(root=root, backups=backups, state=state, env=env)


class Clock:
    """offsite.py's `time`, without the waiting."""

    def __init__(self) -> None:
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(offsite, "time", fake)
    return fake


def events(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def run_offsite(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, list[dict[str, Any]]]:
    capsys.readouterr()
    code = offsite.main(list(args))
    return code, events(capsys.readouterr().out)


def b64_sha256(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


# --- push -----------------------------------------------------------------------


def test_a_backup_is_copied_only_once_s3_holds_exactly_its_bytes(
    host: Host, clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = host.dump()
    manifest = dump.with_name(dump.name + ".manifest.json")

    code, log = run_offsite(capsys, "push", str(dump))

    assert code == 0
    objects = host.s3_objects()
    for path in (dump, manifest):
        stored = objects[f"backups/{path.name}"]
        assert host.s3_bytes(f"backups/{path.name}") == path.read_bytes()
        assert stored["ChecksumSHA256"] == b64_sha256(path.read_bytes())
        assert stored["ServerSideEncryption"] == "AES256"
    puts = host.aws_calls("put-object")
    # The dump first, so a manifest in the bucket always has its dump there.
    assert [call[call.index("--key") + 1] for call in puts] == [
        f"backups/{dump.name}",
        f"backups/{manifest.name}",
    ]
    assert all(call[call.index("--if-none-match") + 1] == "*" for call in puts)
    receipt = json.loads(dump.with_name(dump.name + ".offsite.json").read_text())
    assert [o["object"] for o in receipt["objects"]] == [
        f"backups/{dump.name}",
        f"backups/{manifest.name}",
    ]
    assert all(o["version_id"] for o in receipt["objects"])
    assert log[-1]["event"] == "offsite_copy_verified"
    assert clock.slept == []


def test_the_keys_reach_the_cli_only_as_a_read_only_mount(
    host: Host, clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = host.dump()
    capsys.readouterr()
    assert offsite.main(["push", str(dump)]) == 0
    output = capsys.readouterr()

    calls = host.aws_calls()
    assert calls
    for call in calls:
        assert not any(KEY_ID in arg or SECRET in arg for arg in call)
        assert (
            f"type=bind,source={host.root}/secrets/aws-backup.credentials,target=/run/offsite/credentials,readonly"
            in call
        )
        assert offsite.AWS_CLI_IMAGE in call
        assert "@sha256:" in offsite.AWS_CLI_IMAGE
    for text in (output.out, output.err, (host.backups / f"{dump.name}.offsite.json").read_text()):
        assert KEY_ID not in text
        assert SECRET not in text


def test_s3_reporting_other_bytes_fails_the_copy(
    host: Host, clock: Clock, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dump = host.dump()
    host.faults(monkeypatch, "corrupt-head")

    code, log = run_offsite(capsys, "push", str(dump))

    assert code == 1
    assert log[-1]["event"] == "offsite_failed"
    assert log[-1]["reason"].startswith("remote verification failed")
    assert not any(e["event"] == "offsite_copy_verified" for e in log)
    assert not dump.with_name(dump.name + ".offsite.json").exists()


def test_an_object_s3_stored_unencrypted_fails_the_copy(
    host: Host, clock: Clock, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dump = host.dump()
    host.faults(monkeypatch, "no-sse")

    code, log = run_offsite(capsys, "push", str(dump))

    assert code == 1
    assert log[-1]["s3_encryption"] is None
    assert not dump.with_name(dump.name + ".offsite.json").exists()


def test_pushing_again_sends_nothing_and_still_verifies(
    host: Host, clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = host.dump()
    assert offsite.main(["push", str(dump)]) == 0
    puts_before = len(host.aws_calls("put-object"))

    code, log = run_offsite(capsys, "push", str(dump))

    assert code == 0
    assert len(host.aws_calls("put-object")) == puts_before
    assert [e["event"] for e in log].count("already_present") == 2
    assert [e["event"] for e in log].count("verified") == 2


def test_an_object_already_holding_other_bytes_is_never_replaced(
    host: Host, clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = host.dump()
    impostor = host.backups / "elsewhere" / dump.name
    impostor.parent.mkdir()
    impostor.write_bytes(b"an older file under the same name")
    impostor.with_name(impostor.name + ".manifest.json").write_text(
        dump.with_name(dump.name + ".manifest.json").read_text()
    )
    # Put the impostor in S3 under the dump's name.
    offsite.put_verified(
        offsite.load_config(offsite.Paths(host.root)), impostor, "application/octet-stream"
    )
    puts_before = len(host.aws_calls("put-object"))

    code, log = run_offsite(capsys, "push", str(dump))

    assert code == 1
    assert "refusing to replace it" in log[-1]["reason"]
    assert len(host.aws_calls("put-object")) == puts_before
    assert host.s3_bytes(f"backups/{dump.name}") == b"an older file under the same name"


def test_a_transient_failure_is_retried_until_the_copy_is_verified(
    host: Host, clock: Clock, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dump = host.dump()
    host.faults(monkeypatch, "flaky-put=2")

    code, log = run_offsite(capsys, "push", str(dump))

    assert code == 0
    assert clock.slept == [10.0, 30.0]
    assert [e["attempt"] for e in log if e["event"] == "retry"] == [1, 2]
    assert dump.with_name(dump.name + ".offsite.json").exists()


def test_three_failed_attempts_fail_the_copy(
    host: Host, clock: Clock, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dump = host.dump()
    host.faults(monkeypatch, "flaky-put=3")

    code, log = run_offsite(capsys, "push", str(dump))

    assert code == 1
    assert log[-1]["reason"] == "upload failed"
    assert len(host.aws_calls("put-object")) == 3
    assert not dump.with_name(dump.name + ".offsite.json").exists()


def test_access_denied_is_not_retried(
    host: Host, clock: Clock, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dump = host.dump()
    host.faults(monkeypatch, "deny-put")

    code, log = run_offsite(capsys, "push", str(dump))

    assert code == 1
    assert log[-1]["error"] == "AccessDenied"
    assert len(host.aws_calls("put-object")) == 1
    assert clock.slept == []


def test_a_manifest_describing_other_bytes_stops_the_copy_before_anything_is_sent(
    host: Host, clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = host.dump()
    dump.write_bytes(dump.read_bytes() + b"appended after the manifest was written")

    code, log = run_offsite(capsys, "push", str(dump))

    assert code == 1
    assert log[-1]["reason"] == "the manifest does not describe this file"
    assert host.aws_calls() == []


def test_push_pending_copies_only_what_an_earlier_run_left_behind(
    host: Host, clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    copied = host.dump("20261116T072253Z")
    assert offsite.main(["push", str(copied)]) == 0
    left_behind = host.dump("20261117T072253Z")
    puts_before = len(host.aws_calls("put-object"))

    code, log = run_offsite(capsys, "push-pending", str(host.backups))

    assert code == 0
    new_puts = host.aws_calls("put-object")[puts_before:]
    assert [c[c.index("--key") + 1] for c in new_puts] == [
        f"backups/{left_behind.name}",
        f"backups/{left_behind.name}.manifest.json",
    ]
    assert log[-1] == {**log[-1], "event": "pending_pushed", "pending": 1, "failed": 0}


# --- fetch ----------------------------------------------------------------------


def test_fetch_latest_brings_back_the_newest_backup_and_proves_it(
    host: Host, clock: Clock, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    older = host.dump("20261116T072253Z")
    newer = host.dump("20261117T072253Z")
    for dump in (older, newer):
        assert offsite.main(["push", str(dump)]) == 0
    elsewhere = tmp_path / "recovered"

    code, log = run_offsite(capsys, "fetch", "latest", str(elsewhere))

    assert code == 0
    copy = elsewhere / newer.name
    assert copy.read_bytes() == newer.read_bytes()
    assert (elsewhere / f"{newer.name}.manifest.json").read_bytes() == (
        newer.with_name(newer.name + ".manifest.json").read_bytes()
    )
    # The file restore.sh checks before it restores anything.
    assert (elsewhere / f"{newer.name}.sha256").read_text() == (
        f"{hashlib.sha256(newer.read_bytes()).hexdigest()}  {newer.name}\n"
    )
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600
    assert log[-1]["event"] == "fetched"


def test_a_download_that_differs_from_its_manifest_is_discarded(
    host: Host,
    clock: Clock,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dump = host.dump()
    assert offsite.main(["push", str(dump)]) == 0
    host.faults(monkeypatch, "corrupt-download")
    elsewhere = tmp_path / "recovered"

    code, log = run_offsite(capsys, "fetch", dump.name, str(elsewhere))

    assert code == 1
    assert log[-1]["reason"] == "download failed verification"
    assert list(elsewhere.iterdir()) == []


def test_fetch_never_overwrites_a_file_already_there(
    host: Host, clock: Clock, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    dump = host.dump()
    assert offsite.main(["push", str(dump)]) == 0
    elsewhere = tmp_path / "recovered"
    elsewhere.mkdir()
    (elsewhere / dump.name).write_bytes(b"something precious")

    code, log = run_offsite(capsys, "fetch", dump.name, str(elsewhere))

    assert code == 1
    assert log[-1]["reason"] == "refusing to overwrite files already in the directory"
    assert (elsewhere / dump.name).read_bytes() == b"something precious"
    assert host.aws_calls("get-object") == []


# --- manifest and restore comparison ---------------------------------------------


def test_the_manifest_records_the_file_the_release_and_the_database(host: Host) -> None:
    dump = host.dump()

    manifest = json.loads(dump.with_name(dump.name + ".manifest.json").read_text())

    assert manifest["format"] == "pathable-backup-manifest/1"
    assert manifest["file"] == dump.name
    assert manifest["bytes"] == dump.stat().st_size
    assert manifest["sha256"] == hashlib.sha256(dump.read_bytes()).hexdigest()
    assert manifest["release"]["revision"] == REVISION
    assert manifest["database"]["alembic_revision"] == "0008_place_codes"
    assert manifest["database"]["active_datasets"][0]["content_checksum"] == "d" * 64
    assert manifest["database"]["table_rows"]["graph_edges"] == 180_580
    assert manifest["pg_dump"].startswith("pg_dump (PostgreSQL) 17.5")


def test_a_restore_is_compared_with_its_manifest_table_by_table() -> None:
    restored = json.loads(json.dumps(DATABASE))
    assert offsite.compare_database(DATABASE, restored) == []

    restored["alembic_revision"] = "0007_place_search"
    restored["table_rows"]["graph_edges"] -= 1
    del restored["table_rows"]["gazetteer_entries"]
    restored["active_datasets"][0]["content_checksum"] = "e" * 64

    problems = offsite.compare_database(DATABASE, restored)

    assert problems[0] == "alembic revision: recorded 0008_place_codes, restored 0007_place_search"
    assert "table gazetteer_entries: recorded 33225 rows, restored absent" in problems
    assert "table graph_edges: recorded 180580 rows, restored 180579" in problems
    assert any(p.startswith(f"active dataset {DATASET}:") for p in problems)
    assert len(problems) == 4


def test_verify_restore_fails_on_a_database_unlike_its_manifest(
    host: Host, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = host.dump()
    manifest = dump.with_name(dump.name + ".manifest.json")
    assert offsite.main(["verify-restore", str(manifest), "--container", "scratch"]) == 0
    restored = json.loads(json.dumps(DATABASE))
    restored["table_rows"]["graph_nodes"] = 0
    (host.state / "db.json").write_text(json.dumps(restored))

    code, log = run_offsite(capsys, "verify-restore", str(manifest), "--container", "scratch")

    assert code == 1
    assert log == [
        {
            **log[0],
            "event": "restore_mismatch",
            "detail": "table graph_nodes: recorded 155732 rows, restored 0",
        }
    ]


# --- installing the credentials ---------------------------------------------------


class Terminal:
    def isatty(self) -> bool:
        return True


@pytest.fixture
def at_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(offsite.sys, "stdin", Terminal())


def typed(monkeypatch: pytest.MonkeyPatch, *values: str) -> list[str]:
    """Answer getpass with `values`; return the prompts it was asked."""
    prompts: list[str] = []
    answers = iter(values)

    def getpass(prompt: str = "") -> str:
        prompts.append(prompt)
        return next(answers)

    def no_visible_input(prompt: str = "") -> str:
        raise AssertionError(f"read with echo on: {prompt!r}")

    monkeypatch.setattr(offsite.getpass, "getpass", getpass)
    monkeypatch.setattr("builtins.input", no_visible_input)
    return prompts


not_root = pytest.mark.skipif(
    getattr(os, "geteuid", lambda: 0)() == 0, reason="the installer refuses root by design"
)


@not_root
@pytest.mark.usefixtures("at_a_terminal")
def test_the_keys_are_stored_for_this_user_alone(
    host: Host, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    credentials = host.root / "secrets" / "aws-backup.credentials"
    credentials.unlink()
    prompts = typed(monkeypatch, f"  {KEY_ID} ", f"{SECRET}\r")

    code = offsite.main(["install-credentials", "--no-check"])

    assert code == 0
    assert prompts == ["AWS access key ID: ", "AWS secret access key: "]
    assert credentials.read_text() == (
        f"[default]\naws_access_key_id = {KEY_ID}\naws_secret_access_key = {SECRET}\n"
    )
    info = credentials.stat()
    assert stat.S_IMODE(info.st_mode) == 0o600
    assert info.st_uid == credentials.parent.stat().st_uid
    output = capsys.readouterr()
    assert KEY_ID not in output.out + output.err
    assert SECRET not in output.out + output.err
    assert sorted(p.name for p in credentials.parent.iterdir()) == ["aws-backup.credentials"]


@not_root
@pytest.mark.usefixtures("at_a_terminal")
@pytest.mark.parametrize(
    ("key_id", "secret"),
    [
        ("AKIA" + "Q" * 15, SECRET),
        ("ASIA" + "Q" * 16, SECRET),
        (KEY_ID, SECRET[:-1]),
        (KEY_ID, SECRET[:-1] + "!"),
    ],
    ids=["short key id", "temporary key", "short secret", "secret with a stray character"],
)
def test_a_mistyped_key_stores_nothing(
    host: Host,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    key_id: str,
    secret: str,
) -> None:
    credentials = host.root / "secrets" / "aws-backup.credentials"
    credentials.unlink()
    typed(monkeypatch, key_id, secret)

    code = offsite.main(["install-credentials", "--no-check"])

    assert code == 2
    assert list(credentials.parent.iterdir()) == []
    output = capsys.readouterr()
    assert key_id not in output.out + output.err
    assert secret not in output.out + output.err


@not_root
@pytest.mark.usefixtures("at_a_terminal")
def test_a_terminal_that_cannot_hide_input_is_never_read_from(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    credentials = host.root / "secrets" / "aws-backup.credentials"
    credentials.unlink()

    def getpass_without_echo_control(prompt: str = "") -> str:
        warnings.warn(
            "Can not control echo on the terminal.", offsite.getpass.GetPassWarning, stacklevel=2
        )
        return KEY_ID

    monkeypatch.setattr(offsite.getpass, "getpass", getpass_without_echo_control)

    assert offsite.main(["install-credentials", "--no-check"]) == 2
    assert not credentials.exists()


@not_root
@pytest.mark.usefixtures("at_a_terminal")
def test_declining_to_replace_leaves_the_existing_keys(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    credentials = host.root / "secrets" / "aws-backup.credentials"
    before = credentials.read_bytes()
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    def never(prompt: str = "") -> str:
        raise AssertionError("asked for a key after the operator declined")

    monkeypatch.setattr(offsite.getpass, "getpass", never)

    assert offsite.main(["install-credentials", "--no-check"]) == 1
    assert credentials.read_bytes() == before


@not_root
def test_the_installer_needs_a_terminal(host: Host, monkeypatch: pytest.MonkeyPatch) -> None:
    credentials = host.root / "secrets" / "aws-backup.credentials"
    credentials.unlink()
    typed(monkeypatch, KEY_ID, SECRET)

    class Pipe:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(offsite.sys, "stdin", Pipe())

    assert offsite.main(["install-credentials", "--no-check"]) == 2
    assert not credentials.exists()


@pytest.mark.usefixtures("at_a_terminal")
def test_the_installer_refuses_root(host: Host, monkeypatch: pytest.MonkeyPatch) -> None:
    credentials = host.root / "secrets" / "aws-backup.credentials"
    credentials.unlink()
    typed(monkeypatch, KEY_ID, SECRET)
    monkeypatch.setattr(offsite.os, "geteuid", lambda: 0)

    assert offsite.main(["install-credentials", "--no-check"]) == 2
    assert not credentials.exists()


@not_root
@pytest.mark.usefixtures("at_a_terminal")
def test_a_secrets_directory_others_can_read_is_refused(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    credentials = host.root / "secrets" / "aws-backup.credentials"
    credentials.unlink()
    credentials.parent.chmod(0o755)
    typed(monkeypatch, KEY_ID, SECRET)

    assert offsite.main(["install-credentials", "--no-check"]) == 2
    assert not credentials.exists()


def test_a_credentials_file_others_can_read_is_refused_without_being_read(
    host: Host, capsys: pytest.CaptureFixture[str]
) -> None:
    (host.root / "secrets" / "aws-backup.credentials").chmod(0o644)

    code, log = run_offsite(capsys, "check")

    assert code == 1
    assert log[-1]["reason"] == "the credentials file can be read by others; it must be mode 0600"
    assert host.aws_calls() == []


# --- backup.sh --------------------------------------------------------------------


def backup(
    host: Host, *args: str, offsite_mode: str | None = None
) -> tuple[int, list[dict[str, Any]]]:
    env = dict(host.env)
    if offsite_mode is not None:
        env["PATHABLE_BACKUP_OFFSITE"] = offsite_mode
    proc = subprocess.run(
        ["sh", str(VPS / "backup.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    # Every line it prints is one JSON object.
    return proc.returncode, events(proc.stdout)


def the_new_dump(host: Host) -> Path:
    (dump,) = (
        p for p in host.backups.glob("pathable-*.dump") if not p.name.startswith("pathable-202610")
    )
    return dump


def test_a_scheduled_backup_succeeds_only_with_its_verified_off_host_copy(host: Host) -> None:
    code, log = backup(host, "scheduled", offsite_mode="required")

    assert code == 0
    dump = the_new_dump(host)
    for suffix in ("", ".sha256", ".manifest.json", ".offsite.json"):
        assert dump.with_name(dump.name + suffix).is_file()
    assert set(host.s3_objects()) == {f"backups/{dump.name}", f"backups/{dump.name}.manifest.json"}
    assert log[-1] == {**log[-1], "event": "backup_complete", "offsite": "verified"}
    assert all(e["component"] in ("backup", "offsite") for e in log)
    assert SECRET not in json.dumps(log)


def test_a_failed_off_host_copy_fails_the_backup_and_keeps_the_local_one(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    host.faults(monkeypatch, "corrupt-head")

    code, log = backup(host, "scheduled", offsite_mode="required")

    assert code == 1
    dump = the_new_dump(host)
    assert dump.with_name(dump.name + ".manifest.json").is_file()
    assert not dump.with_name(dump.name + ".offsite.json").exists()
    assert log[-1] == {**log[-1], "event": "backup_failed", "offsite": "failed"}
    assert not any(e["event"] == "backup_complete" for e in log)


def test_a_required_copy_with_nothing_configured_fails_after_the_local_backup(host: Host) -> None:
    (host.root / "offsite.env").unlink()

    code, log = backup(host, "scheduled", offsite_mode="required")

    assert code == 1
    assert the_new_dump(host).is_file()
    assert log[-1]["event"] == "backup_failed"
    assert host.aws_calls() == []


def test_without_configuration_the_backup_says_it_is_local_only(host: Host) -> None:
    (host.root / "offsite.env").unlink()

    code, log = backup(host, "manual")

    assert code == 0
    assert log[-1] == {
        **log[-1],
        "event": "backup_complete",
        "offsite": "not configured: local copy only",
    }
    assert host.aws_calls() == []


def test_a_failed_manifest_fails_the_backup(host: Host, monkeypatch: pytest.MonkeyPatch) -> None:
    host.faults(monkeypatch, "psql-fails")

    code, log = backup(host, "scheduled", offsite_mode="required")

    assert code == 1
    assert log[-1] == {**log[-1], "event": "backup_failed", "stage": "manifest"}
    assert host.aws_calls() == []


def test_retention_removes_an_expired_backup_with_its_manifest_and_receipt(host: Host) -> None:
    old: list[Path] = []
    twenty_days_ago = time.time() - 20 * 86_400
    for day in range(1, 5):
        dump = host.backups / f"pathable-2026100{day}T071500Z-scheduled-{REVISION[:12]}.dump"
        for suffix in ("", ".sha256", ".manifest.json", ".offsite.json"):
            sidecar = dump.with_name(dump.name + suffix)
            sidecar.write_text("old")
            os.utime(sidecar, (twenty_days_ago + day, twenty_days_ago + day))
        old.append(dump)

    code, log = backup(host, "scheduled", offsite_mode="required")

    assert code == 0
    remaining = sorted(p.name for p in host.backups.iterdir())
    for dump in old[:2]:
        assert not any(name.startswith(dump.name) for name in remaining)
    for dump in old[2:]:
        assert sum(name.startswith(dump.name) for name in remaining) == 4
    assert [e["file"] for e in log if e["event"] == "expired"] == [old[0].name, old[1].name]


def test_the_timer_requires_the_off_host_copy() -> None:
    unit = (VPS / "systemd" / "pathable-backup.service").read_text()

    assert "Environment=PATHABLE_BACKUP_OFFSITE=required" in unit
    assert "TimeoutStartSec=" in unit


# --- deploy.sh and restore.sh: the backup before production changes -------------
#
# Found in review on 2026-11-19: deploy.sh ran `backup.sh pre-deploy` without
# asking for the off-host copy, and backup.sh's default (auto) keeps a backup
# local whenever offsite.env is absent. A host that had lost that file would have
# deployed with its only way back sitting on itself.

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return proc.stdout.strip()


@pytest.fixture
def production(host: Host) -> None:
    """The site and database settings deploy.sh and restore.sh read."""
    (host.root / "site.env").write_text(
        "PATHABLE_HOST=pilot.test\nPATHABLE_TLS_PROFILE=shortlived\n"
    )
    database_secrets = host.root / "secrets" / "pathable.env"
    database_secrets.write_text("PATHABLE_DB_PASSWORD=" + "p" * 24 + "\n")
    database_secrets.chmod(0o600)


@pytest.fixture
def release(host: Host, production: None) -> str:
    """A checkout whose origin/main holds a release, as deploy.sh needs. Its SHA."""
    origin = host.root.parent / "origin.git"
    repo = host.root / "repo"
    git("init", "--quiet", "--bare", "--initial-branch=main", str(origin))
    git("init", "--quiet", "--initial-branch=main", str(repo))
    pyproject = repo / "services" / "api" / "pyproject.toml"
    pyproject.parent.mkdir(parents=True)
    pyproject.write_text('[project]\nname = "pathable-api"\nversion = "0.1.0"\n')
    git("-C", str(repo), "add", ".")
    git("-C", str(repo), "commit", "--quiet", "-m", "a release")
    git("-C", str(repo), "remote", "add", "origin", str(origin))
    git("-C", str(repo), "push", "--quiet", "origin", "main")
    return git("-C", str(repo), "rev-parse", "HEAD")


@dataclass
class Run:
    code: int
    log: list[str]
    """The script's own lines."""
    events: list[dict[str, Any]]
    """backup.sh's and offsite.py's JSON lines."""
    stderr: str


def run_script(host: Host, script: str, *args: str, offsite_mode: str | None = None) -> Run:
    env = {**host.env, **GIT_ENV}
    env.pop("PATHABLE_BACKUP_OFFSITE", None)
    if offsite_mode is not None:
        env["PATHABLE_BACKUP_OFFSITE"] = offsite_mode
    proc = subprocess.run(
        ["sh", str(VPS / script), *args],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    lines = proc.stdout.splitlines()
    return Run(
        code=proc.returncode,
        log=[line for line in lines if not line.startswith("{")],
        events=[json.loads(line) for line in lines if line.startswith("{")],
        stderr=proc.stderr,
    )


def deploy(host: Host, offsite_mode: str | None = None) -> Run:
    return run_script(host, "deploy.sh", "up", "origin/main", offsite_mode=offsite_mode)


def restore_into_production(host: Host, dump: Path, offsite_mode: str | None = None) -> Run:
    args = ["--into", "production", "--yes", "--dump", str(dump)]
    return run_script(host, "restore.sh", *args, offsite_mode=offsite_mode)


def first_switch(host: Host) -> int | None:
    """Where deploy.sh first touched the running stack, if it did."""
    return next((i for i, call in enumerate(host.calls()) if call[0] == "compose"), None)


def serving(host: Host) -> str:
    return (host.root / "release.env").read_text()


def history(host: Host) -> list[str]:
    return (host.root / "deployments.log").read_text().splitlines()


def test_a_deploy_switches_only_once_s3_holds_its_backup(host: Host, release: str) -> None:
    run = deploy(host)

    assert run.code == 0, run
    dump = the_new_dump(host)
    assert "-pre-deploy-" in dump.name
    assert set(host.s3_objects()) == {f"backups/{dump.name}", f"backups/{dump.name}.manifest.json"}
    assert dump.with_name(dump.name + ".offsite.json").is_file()
    s3_requests = host.aws_calls()
    last_s3_request = max(i for i, call in enumerate(host.calls()) if call in s3_requests)
    switch = first_switch(host)
    assert switch is not None
    assert last_s3_request < switch
    assert f"PATHABLE_REVISION={release}\n" in serving(host)
    assert history(host)[-1].endswith(" backup=verified-in-s3")


def test_a_deploy_without_offsite_env_no_longer_falls_back_to_a_local_backup(
    host: Host, release: str
) -> None:
    (host.root / "offsite.env").unlink()
    before = serving(host)

    run = deploy(host)

    assert run.code == 1, run
    # Refused before building, backing up or switching anything.
    assert host.calls() == []
    assert list(host.backups.iterdir()) == []
    assert serving(host) == before
    assert not (host.root / "deployments.log").exists()
    assert "offsite.env is missing" in run.log[-1]
    assert "PATHABLE_BACKUP_OFFSITE=skip" in run.log[-1]


@pytest.mark.parametrize("breakage", ["s3-holds-other-bytes", "access-denied", "no-key"])
def test_a_deploy_whose_backup_s3_does_not_hold_is_not_switched(
    host: Host, release: str, monkeypatch: pytest.MonkeyPatch, breakage: str
) -> None:
    if breakage == "no-key":
        (host.root / "secrets" / "aws-backup.credentials").unlink()
    else:
        fault = {"s3-holds-other-bytes": "corrupt-head", "access-denied": "deny-put"}[breakage]
        host.faults(monkeypatch, fault)
    before = serving(host)

    run = deploy(host)

    assert run.code == 1, run
    assert first_switch(host) is None
    dump = the_new_dump(host)
    assert not dump.with_name(dump.name + ".offsite.json").exists()
    assert run.events[-1] == {**run.events[-1], "event": "backup_failed", "label": "pre-deploy"}
    assert serving(host) == before
    assert not (host.root / "deployments.log").exists()
    assert run.log[-1].endswith(
        f"{release} was not deployed; the release that was serving still is"
    )
    assert any("PATHABLE_BACKUP_OFFSITE=skip" in line for line in run.log)


def test_skip_deploys_on_a_local_backup_and_says_so_in_every_log(host: Host, release: str) -> None:
    (host.root / "offsite.env").unlink()

    run = deploy(host, offsite_mode="skip")

    assert run.code == 0, run
    assert "WARNING: PATHABLE_BACKUP_OFFSITE=skip" in run.log[0]
    assert [e["event"] for e in run.events] == [
        "offsite_skipped",
        "manifest_written",
        "local_backup_written",
        "backup_complete",
    ]
    assert run.events[-1]["offsite"] == "skipped (PATHABLE_BACKUP_OFFSITE=skip)"
    assert host.aws_calls() == []
    assert f"PATHABLE_REVISION={release}\n" in serving(host)
    assert history(host)[-1].endswith(" backup=local-only")


@pytest.mark.parametrize("mode", ["auto", "Skip"])
def test_a_deploy_takes_no_other_backup_mode(host: Host, release: str, mode: str) -> None:
    # auto is backup.sh's own default, the one that falls back to local only.
    run = deploy(host, offsite_mode=mode)

    assert run.code == 1, run
    assert host.calls() == []
    assert f"PATHABLE_BACKUP_OFFSITE={mode}." in run.log[-1]


@pytest.mark.usefixtures("production")
def test_a_production_restore_stops_nothing_until_s3_holds_its_safety_backup(
    host: Host,
) -> None:
    dump = host.dump()
    (host.root / "offsite.env").unlink()

    run = restore_into_production(host, dump)

    assert run.code == 1, run
    assert not any(call[0] == "stop" for call in host.calls())
    assert run.events[-1] == {**run.events[-1], "event": "backup_failed", "label": "pre-restore"}
    assert run.log[-1].endswith("Nothing was stopped or replaced")


@pytest.mark.usefixtures("production")
def test_skip_lets_a_production_restore_go_ahead_on_a_local_safety_backup(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    dump = host.dump()
    (host.root / "offsite.env").unlink()
    host.faults(monkeypatch, "stop-fails")

    run = restore_into_production(host, dump, offsite_mode="skip")

    assert any("WARNING: PATHABLE_BACKUP_OFFSITE=skip" in line for line in run.log)
    assert [e["event"] for e in run.events] == [
        "offsite_skipped",
        "manifest_written",
        "local_backup_written",
        "backup_complete",
    ]
    assert host.aws_calls() == []
    # It went on to stop the API, which this fake refuses, so it ends there.
    assert ["stop", "pathable-api-1"] in host.calls()
    assert run.code == 1
