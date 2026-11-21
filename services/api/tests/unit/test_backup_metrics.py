"""What Alloy is told about the backups is true, and tells nothing else.

infra/vps/backup_metrics.py turns the backup unit's runs and the receipts beside
the dumps into the status the backup alerts read
(infra/vps/grafana/backup.rules.yaml). What it must never do: count a copy S3
was never shown to hold (a local-only or skipped backup, a receipt for other
bytes); let a late copy of an old dump pass for a fresh backup; call a failed
run a success; or put a bucket, object name, path or AWS message where Alloy
would send it. The end-to-end runs, through the real backup.sh, are in
test_offsite_backup.py.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
import stat
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from prometheus_client.parser import text_string_to_metric_families

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the tooling runs on Linux hosts")

REPO = Path(__file__).resolve().parents[4]
VPS = REPO / "infra" / "vps"
BACKUP_RULES = VPS / "grafana" / "backup.rules.yaml"
ALLOY = VPS / "alloy" / "config.alloy"
COMPOSE = VPS / "compose.yaml"
UNIT = VPS / "systemd" / "pathable-backup.service"

BUCKET = "pathable-test-bucket"
REVISION = "a1" * 6
INVOCATION_ID = "0123456789abcdef0123456789abcdef"


def _module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# backup_metrics.py imports offsite.py by that name, as it does beside it on the host.
offsite = _module("offsite", VPS / "offsite.py")
metrics = _module("pathable_vps_backup_metrics_under_test", VPS / "backup_metrics.py")


def epoch(stamp: str) -> float:
    return dt.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC).timestamp()


def make_dump(
    backups: Path,
    stamp: str,
    *,
    manifest: bool = True,
    receipt: dict[str, Any] | None = None,
    receipted: bool = True,
) -> Path:
    """A dump as backup.sh and offsite.py leave it: manifest, then receipt once S3 holds it."""
    name = f"pathable-{stamp}-scheduled-{REVISION}.dump"
    dump = backups / name
    dump.write_bytes(b"PGDMP" + stamp.encode())
    sha256 = offsite.file_sha256(dump)
    if manifest:
        (backups / f"{name}.manifest.json").write_text(
            json.dumps({"format": offsite.MANIFEST_FORMAT, "file": name, "sha256": sha256})
        )
    if receipted:
        body = {
            "format": offsite.RECEIPT_FORMAT,
            "file": name,
            "bucket": BUCKET,
            "region": "ca-central-1",
            "objects": [
                {"object": f"backups/{name}", "sha256": sha256, "version_id": "v1"},
                {"object": f"backups/{name}.manifest.json", "sha256": "b" * 64, "version_id": "v2"},
            ],
            "verified_at": "2026-11-21T07:15:30Z",
        }
        (backups / f"{name}.offsite.json").write_text(json.dumps(receipt or body))
    return dump


@pytest.fixture
def places(tmp_path: Path) -> Any:
    backups = tmp_path / "backups"
    backups.mkdir()
    return metrics.Places(metrics=tmp_path / "srv" / "metrics", backups=backups)


def samples(text: str) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    """Each sample of an exposition, parsed as Prometheus (and Alloy) parse it."""
    found: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
    for family in text_string_to_metric_families(text):
        assert family.type == "gauge", family.name
        for sample in family.samples:
            found[(sample.name, tuple(sorted(sample.labels.items())))] = sample.value
    return found


def value(text: str, name: str) -> float:
    (found,) = [v for (n, _), v in samples(text).items() if n == name]
    return found


# --- what counts as a verified copy -------------------------------------------


def test_only_a_backup_s3_is_verified_to_hold_counts(places: Any) -> None:
    make_dump(places.backups, "20261118T071500Z")
    # Its copy failed, or was skipped on purpose: a manifest, no receipt.
    make_dump(places.backups, "20261119T071500Z", receipted=False)
    # Before the off-host copy existed: no manifest either.
    make_dump(places.backups, "20261120T071500Z", manifest=False, receipted=False)

    found = metrics.status(places.backups)

    assert found.last_verified_dump == epoch("20261118T071500Z")
    assert found.last_local_dump == epoch("20261120T071500Z")
    assert (found.verified, found.pending, found.unrecognised_receipts) == (1, 1, 0)


def test_a_late_copy_of_an_old_dump_does_not_pass_for_a_fresh_backup(places: Any) -> None:
    # Copied today by a retry, taken three days ago: three days old is what counts.
    make_dump(places.backups, "20261118T071500Z")

    found = metrics.status(places.backups)

    assert found.last_verified_dump == epoch("20261118T071500Z")


def _receipt_for_other_bytes(name: str) -> dict[str, Any]:
    return {
        "format": offsite.RECEIPT_FORMAT,
        "file": name,
        "objects": [
            {"object": f"backups/{name}", "sha256": "c" * 64},
            {"object": f"backups/{name}.manifest.json", "sha256": "b" * 64},
        ],
    }


@pytest.mark.parametrize(
    "receipt",
    [
        pytest.param(_receipt_for_other_bytes, id="other-bytes"),
        pytest.param(
            lambda name: {**_receipt_for_other_bytes(name), "file": "pathable-other.dump"},
            id="another-file",
        ),
        pytest.param(
            lambda name: {**_receipt_for_other_bytes(name), "format": "pathable-offsite-receipt/0"},
            id="unknown-format",
        ),
        pytest.param(
            lambda name: {**_receipt_for_other_bytes(name), "objects": []}, id="no-objects"
        ),
    ],
)
def test_a_receipt_that_does_not_describe_its_dump_is_not_a_copy(places: Any, receipt: Any) -> None:
    name = f"pathable-20261120T071500Z-scheduled-{REVISION}.dump"
    make_dump(places.backups, "20261120T071500Z", receipt=receipt(name))

    found = metrics.status(places.backups)

    assert found.last_verified_dump == 0
    assert (found.verified, found.unrecognised_receipts) == (0, 1)


def test_a_receipt_that_is_not_json_or_has_lost_its_manifest_is_not_a_copy(places: Any) -> None:
    unreadable = make_dump(places.backups, "20261119T071500Z")
    unreadable.with_name(unreadable.name + ".offsite.json").write_text("{not json")
    orphaned = make_dump(places.backups, "20261120T071500Z")
    orphaned.with_name(orphaned.name + ".manifest.json").unlink()

    found = metrics.status(places.backups)

    assert found.last_verified_dump == 0
    assert found.unrecognised_receipts == 2


def test_no_backups_at_all_reads_as_never_verified(places: Any) -> None:
    exposition = metrics.offsite_exposition(metrics.status(places.backups), now=1_795_000_000.0)

    assert value(exposition, "pathable_backup_offsite_last_verified_dump_timestamp_seconds") == 0
    assert value(exposition, "pathable_backup_local_last_dump_timestamp_seconds") == 0


# --- where a failed run stopped -------------------------------------------------


def failed(**fields: Any) -> dict[str, Any]:
    return {"component": "backup", "event": "backup_failed", "label": "scheduled", **fields}


def offsite_failed(reason: str) -> dict[str, Any]:
    return {"component": "offsite", "event": "offsite_failed", "reason": reason}


@pytest.mark.parametrize(
    ("service_result", "events", "stage"),
    [
        ("success", [], "none"),
        ("timeout", [], "timeout"),
        ("exit-code", [failed(stage="pg_dump")], "dump"),
        ("exit-code", [failed(stage="pg_restore-list")], "dump"),
        ("exit-code", [failed(stage="manifest")], "dump"),
        ("exit-code", [offsite_failed("upload failed"), failed(offsite="failed")], "upload"),
        ("exit-code", [offsite_failed("AWS request failed"), failed(offsite="failed")], "upload"),
        ("exit-code", [offsite_failed(offsite.NOT_HELD), failed(offsite="failed")], "verification"),
        (
            "exit-code",
            [offsite_failed(offsite.NAME_TAKEN), failed(offsite="failed")],
            "verification",
        ),
        (
            "exit-code",
            [failed(reason="PATHABLE_BACKUP_OFFSITE must be auto, required or skip")],
            "other",
        ),
        # Nothing in the journal says why: never guessed into a stage.
        ("exit-code", [], "other"),
        ("signal", [], "other"),
    ],
)
def test_where_a_failed_run_stopped(
    service_result: str, events: list[dict[str, Any]], stage: str
) -> None:
    assert metrics.stage_of(service_result, events) == stage


# --- the files Alloy reads ------------------------------------------------------


@pytest.fixture
def journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `journalctl` on PATH that prints this run's lines, once asked for exactly this run."""
    lines = tmp_path / "journal.jsonl"
    lines.write_text("")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "journalctl"
    fake.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{tmp_path}/journalctl.calls"\n'
        f'[ "$4" = "_SYSTEMD_INVOCATION_ID={INVOCATION_ID}" ] || exit 1\n'
        f'cat "{lines}"\n'
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setattr(metrics, "JOURNAL_RETRY_SECONDS", 0.0)
    return lines


def unit_env(result: str, **extra: str) -> dict[str, str]:
    return {"SERVICE_RESULT": result, "INVOCATION_ID": INVOCATION_ID, **extra}


def test_a_failed_run_is_recorded_as_one_with_its_stage(
    places: Any, journal: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_dump(places.backups, "20261120T071500Z")
    journal.write_text(
        "\n".join(
            json.dumps(e) for e in [offsite_failed("upload failed"), failed(offsite="failed")]
        )
    )

    code = metrics.record_run(places, unit_env("exit-code", EXIT_STATUS="1"), now=1_795_000_000.0)

    assert code == 0
    run = (places.metrics / "backup-run.prom").read_text()
    assert samples(run)[("pathable_backup_last_run_success", (("stage", "upload"),))] == 0
    assert value(run, "pathable_backup_last_run_end_timestamp_seconds") == 1_795_000_000
    # The copy status is refreshed too: the day before's verified dump still counts.
    copy = (places.metrics / "backup-offsite.prom").read_text()
    assert value(copy, "pathable_backup_offsite_last_verified_dump_timestamp_seconds") == epoch(
        "20261120T071500Z"
    )
    log = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert log[0] == {
        **log[0],
        "event": "run_recorded",
        "service_result": "exit-code",
        "exit_status": "1",
        "stage": "upload",
        "success": 0,
    }


def test_a_successful_run_is_recorded_as_one(places: Any, journal: Path) -> None:
    code = metrics.record_run(places, unit_env("success", EXIT_STATUS="0"), now=1_795_000_000.0)

    assert code == 0
    run = (places.metrics / "backup-run.prom").read_text()
    assert samples(run)[("pathable_backup_last_run_success", (("stage", "none"),))] == 1
    # A success needs no reason, so the journal is not read.
    assert not (journal.parent / "journalctl.calls").exists()


def test_journal_lines_indexed_late_are_waited_for(places: Any, journal: Path) -> None:
    # journald can index a run's last line just after the process exits. The
    # fake prints nothing on the first call and the run's lines from the second.
    events = "\n".join(
        json.dumps(e) for e in [offsite_failed(offsite.NOT_HELD), failed(offsite="failed")]
    )
    fake = journal.parent / "bin" / "journalctl"
    fake.write_text(
        "#!/bin/sh\n"
        f'calls="{journal.parent}/journalctl.calls"\n'
        'echo "$*" >> "$calls"\n'
        '[ "$(wc -l < "$calls")" -ge 2 ] || exit 0\n'
        f"cat <<'EOF'\n{events}\nEOF\n"
    )

    metrics.record_run(places, unit_env("exit-code"), now=1_795_000_000.0)

    run = (places.metrics / "backup-run.prom").read_text()
    assert ("pathable_backup_last_run_success", (("stage", "verification"),)) in samples(run)
    assert len((journal.parent / "journalctl.calls").read_text().splitlines()) == 2


def test_an_invocation_id_that_is_not_systemds_never_reaches_journalctl(
    places: Any, journal: Path
) -> None:
    metrics.record_run(
        places, {"SERVICE_RESULT": "exit-code", "INVOCATION_ID": "x _SYSTEMD_UNIT=other"}, now=1.0
    )

    assert not (journal.parent / "journalctl.calls").exists()
    run = (places.metrics / "backup-run.prom").read_text()
    assert ("pathable_backup_last_run_success", (("stage", "other"),)) in samples(run)


def test_outside_the_unit_record_run_writes_nothing(places: Any) -> None:
    # Run by hand, there is no systemd result to record; inventing one would
    # clear a real failure.
    assert metrics.record_run(places, {}, now=1.0) == 2
    assert not places.metrics.exists()


def test_the_status_names_no_bucket_object_path_or_aws_message(
    places: Any, journal: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = make_dump(places.backups, "20261120T071500Z")
    detail = f"An error occurred (AccessDenied) when calling the PutObject operation: arn:aws:s3:::{BUCKET}/backups/{dump.name}"
    journal.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    **offsite_failed("upload failed"),
                    "object": f"backups/{dump.name}",
                    "detail": detail,
                },
                failed(offsite="failed", file=dump.name),
            ]
        )
    )

    metrics.record_run(places, unit_env("exit-code"), now=1_795_000_000.0)

    written = "".join(p.read_text() for p in places.metrics.iterdir()) + capsys.readouterr().out
    for secret in (
        BUCKET,
        "backups/",
        dump.name,
        "AccessDenied",
        str(places.backups),
        str(places.metrics),
    ):
        assert secret not in written


def test_every_sample_is_a_gauge_from_a_fixed_vocabulary(places: Any, journal: Path) -> None:
    make_dump(places.backups, "20261120T071500Z")
    make_dump(places.backups, "20261121T071500Z", receipted=False)
    journal.write_text(json.dumps(failed(stage="pg_dump")))

    metrics.record_run(places, unit_env("exit-code"), now=1_795_000_000.5)

    names = set()
    for path in places.metrics.iterdir():
        for (name, labels), sample in samples(path.read_text()).items():
            names.add(name)
            assert sample == sample  # not NaN
            for key, label_value in labels:
                assert key == "stage"
                assert label_value in metrics.STAGES
    assert names == {
        "pathable_backup_last_run_success",
        "pathable_backup_last_run_end_timestamp_seconds",
        "pathable_backup_offsite_last_verified_dump_timestamp_seconds",
        "pathable_backup_local_last_dump_timestamp_seconds",
        "pathable_backup_offsite_verified_dumps",
        "pathable_backup_offsite_pending_dumps",
        "pathable_backup_status_refreshed_timestamp_seconds",
    }


def test_alloy_can_read_the_status_and_finds_only_whole_files(places: Any, journal: Path) -> None:
    metrics.record_run(places, unit_env("success"), now=1.0)

    assert stat.S_IMODE(places.metrics.stat().st_mode) & 0o755 == 0o755
    assert sorted(p.name for p in places.metrics.iterdir()) == [
        "backup-offsite.prom",
        "backup-run.prom",
    ]
    for path in places.metrics.iterdir():
        # Alloy reads as its own user (uid 473); a 0600 file would read as an
        # empty status and fire the silence alert.
        assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_a_status_that_cannot_be_written_fails_without_naming_where(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocked = tmp_path / "metrics"
    blocked.write_text("a file where the directory should be")

    code = metrics.main(
        ["refresh"],
        environ={"PATHABLE_METRICS_DIR": str(blocked), "PATHABLE_BACKUP_DIR": str(tmp_path)},
    )

    assert code == 1
    (event,) = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert event["event"] == "status_not_written"
    assert str(tmp_path) not in json.dumps(event)


# --- the status, Alloy and the alerts agree ---------------------------------------


def _rule_series() -> set[str]:
    return set(
        re.findall(r"\b(pathable_backup_[a-z_]+|node_textfile_[a-z_]+)\b", BACKUP_RULES.read_text())
    )


def _block(config: str, header: str) -> str:
    match = re.search(rf"^{re.escape(header)} \{{\n(.*?)^\}}", config, re.M | re.S)
    assert match is not None, header
    return match.group(1)


def test_every_series_the_backup_alerts_read_is_one_the_status_or_alloy_provides(
    places: Any, journal: Path
) -> None:
    metrics.record_run(places, unit_env("success"), now=1.0)
    written = {name for p in places.metrics.iterdir() for (name, _) in samples(p.read_text())}

    assert _rule_series()
    # node_textfile_scrape_error is the collector's own.
    assert _rule_series() <= written | {"node_textfile_scrape_error"}


def test_alloy_forwards_the_status_and_drops_the_series_that_carries_a_path() -> None:
    relabel = _block(ALLOY.read_text(), 'prometheus.relabel "backup"')
    keep = re.search(r'regex\s*=\s*"([^"]+)"', relabel)
    assert keep is not None
    assert 'action        = "keep"' in relabel

    for name in _rule_series():
        assert re.fullmatch(keep.group(1), name), name
    # Its `file` label is the status file's path.
    assert not re.fullmatch(keep.group(1), "node_textfile_mtime_seconds")
    assert not re.fullmatch(keep.group(1), "node_scrape_collector_success")


def test_the_job_the_alerts_name_is_the_one_alloy_gives_the_status() -> None:
    jobs = set(re.findall(r'job="([^"]+)"', BACKUP_RULES.read_text()))
    relabel = _block(ALLOY.read_text(), 'discovery.relabel "backup"')

    assert jobs == {"pathable-backup"}
    assert 'replacement  = "pathable-backup"' in relabel
    # A stable instance: the exporter's default is the container's hostname,
    # which changes on every recreate and would break the series in two.
    assert 'replacement  = "waterloo-pilot"' in relabel


def test_alloy_reads_only_the_status_directory_and_reads_it_read_only() -> None:
    exporter = _block(ALLOY.read_text(), 'prometheus.exporter.unix "backup"')
    compose = COMPOSE.read_text()
    alloy = re.search(r"^  alloy:\n(.*?)(?=^[a-z])", compose, re.M | re.S)
    assert alloy is not None

    assert 'set_collectors = ["textfile"]' in exporter
    assert 'directory = "/var/lib/pathable-metrics"' in exporter
    mount = re.search(r"- type: bind\n(.*?)create_host_path: false", alloy.group(1), re.S)
    assert mount is not None
    assert "source: ../metrics" in mount.group(1)
    assert "target: /var/lib/pathable-metrics" in mount.group(1)
    assert "read_only: true" in mount.group(1)
    # Never the backups, never the AWS key.
    assert "/var/backups" not in alloy.group(1)
    assert "aws-backup.credentials" not in alloy.group(1)


def test_deploy_creates_the_status_directory_as_the_backups_user() -> None:
    # compose refuses a missing source (create_host_path: false) rather than let
    # Docker create it as root, where the unit, as ubuntu, could not write.
    assert '"$ROOT/metrics"' in (VPS / "deploy.sh").read_text()


def test_every_run_of_the_unit_records_how_it_ended_without_changing_it() -> None:
    unit = UNIT.read_text()

    assert (
        "ExecStopPost=-/usr/bin/python3 /srv/pathable/repo/infra/vps/backup_metrics.py record-run"
        in unit.splitlines()
    )


def test_load_alerts_loads_the_backup_rules() -> None:
    assert '"$HERE/backup.rules.yaml"' in (VPS / "grafana" / "load-alerts.sh").read_text()
