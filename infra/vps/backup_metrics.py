#!/usr/bin/env python3
"""Backup status for the metrics agent, written after every run of the backup unit.

    backup_metrics.py record-run
        The backup unit's ExecStopPost. How the run ended comes from systemd
        ($SERVICE_RESULT), and where a failed run stopped from that run's own
        journal lines ($INVOCATION_ID). Writes backup-run.prom, then refreshes.
    backup_metrics.py refresh
        Writes backup-offsite.prom from the receipts beside the dumps.

Only a receipt counts as an off-host copy: offsite.py writes one only once S3,
read back, holds exactly the dump and its manifest, encrypted. A backup kept
local, deliberately (PATHABLE_BACKUP_OFFSITE=skip) or because its copy failed,
has none and moves nothing here. The time that counts is when that backup was
taken, not when it was copied, so a days-old dump copied late never passes for
a fresh one.

Both files go to PATHABLE_METRICS_DIR (default ROOT/metrics), which compose
mounts read-only into Alloy; its textfile collector reads every *.prom there.
Alloy never sees the backups themselves. Every value is a number and every
label comes from a fixed vocabulary: no bucket, object or file name, path, AWS
message or database content is ever written to these files or to the log.

The alerts on these series: infra/vps/grafana/backup.rules.yaml.

Output is one JSON object per line.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import offsite

RUN_FILE = "backup-run.prom"
OFFSITE_FILE = "backup-offsite.prom"
#: Where a failed run stopped. dump: the database side (pg_dump, its check,
#: the manifest). upload: the off-host copy, before S3 held it (credentials,
#: configuration, a refused or failed request). verification: S3 answered but
#: does not hold these bytes. timeout: systemd stopped it. other: anything else.
STAGES = ("none", "dump", "upload", "verification", "timeout", "other")
DUMP_STAGES = frozenset({"pg_dump", "pg_restore-list", "manifest"})
VERIFICATION_REASONS = frozenset({offsite.NAME_TAKEN, offsite.NOT_HELD})

STAMP = re.compile(r"pathable-(\d{8}T\d{6}Z)-")
SHA256 = re.compile(r"[0-9a-f]{64}")
INVOCATION = re.compile(r"[0-9a-f]{32}")
SYSTEMD_WORD = re.compile(r"[a-z-]{1,32}")
EXIT_STATUS = re.compile(r"[A-Z0-9]{1,16}")
#: journald can index a run's last lines a moment after its process exits.
JOURNAL_ATTEMPTS = 5
JOURNAL_RETRY_SECONDS = 1.0
JOURNAL_TIMEOUT_SECONDS = 30


def emit(event: str, **fields: Any) -> None:
    record = {"ts": offsite.utc_now(), "component": "backup-metrics", "event": event, **fields}
    print(json.dumps(record, separators=(",", ":")), flush=True)


# --- what the backups directory says ------------------------------------------


@dataclass(frozen=True)
class Status:
    #: When the newest backup that S3 is verified to hold was taken; 0 if none.
    last_verified_dump: float
    #: When the newest backup on this host was taken, copied or not; 0 if none.
    last_local_dump: float
    verified: int
    pending: int
    #: Receipts that do not describe their dump; never counted as copies.
    unrecognised_receipts: int


def taken_at(name: str) -> float | None:
    """When backup.sh started the dump, from the UTC stamp in its name."""
    match = STAMP.match(name)
    if match is None:
        return None
    try:
        return dt.datetime.strptime(match.group(1) + "+0000", "%Y%m%dT%H%M%SZ%z").timestamp()
    except ValueError:
        return None


def _json_object(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def receipt_describes(dump: Path) -> bool:
    """Whether the dump's receipt records verified copies of it and of its manifest."""
    receipt = _json_object(dump.with_name(dump.name + offsite.RECEIPT_SUFFIX))
    manifest = _json_object(dump.with_name(dump.name + offsite.MANIFEST_SUFFIX))
    if receipt is None or manifest is None:
        return False
    objects = receipt.get("objects")
    if (
        receipt.get("format") != offsite.RECEIPT_FORMAT
        or receipt.get("file") != dump.name
        or not isinstance(objects, list)
    ):
        return False
    copied = {
        str(o.get("object", "")).rsplit("/", 1)[-1]: str(o.get("sha256", ""))
        for o in objects
        if isinstance(o, dict)
    }
    dump_sha256 = copied.get(dump.name, "")
    return (
        SHA256.fullmatch(dump_sha256) is not None
        and dump_sha256 == manifest.get("sha256")
        and SHA256.fullmatch(copied.get(dump.name + offsite.MANIFEST_SUFFIX, "")) is not None
    )


def status(backups: Path) -> Status:
    last_verified = last_local = 0.0
    verified = pending = unrecognised = 0
    for dump in sorted(backups.glob("pathable-*.dump")):
        taken = taken_at(dump.name) if offsite.DUMP_NAME.fullmatch(dump.name) else None
        if taken is None:
            continue
        last_local = max(last_local, taken)
        if dump.with_name(dump.name + offsite.RECEIPT_SUFFIX).exists():
            if receipt_describes(dump):
                verified += 1
                last_verified = max(last_verified, taken)
            else:
                unrecognised += 1
        elif dump.with_name(dump.name + offsite.MANIFEST_SUFFIX).is_file():
            pending += 1
    return Status(last_verified, last_local, verified, pending, unrecognised)


# --- how the unit's run ended ---------------------------------------------------


def run_events(invocation: str | None) -> list[dict[str, Any]]:
    """The backup's and offsite.py's own lines from this run of the unit."""
    if not invocation or INVOCATION.fullmatch(invocation) is None:
        return []
    found: list[dict[str, Any]] = []
    for attempt in range(JOURNAL_ATTEMPTS):
        if attempt:
            time.sleep(JOURNAL_RETRY_SECONDS)
        try:
            journal = subprocess.run(  # noqa: S603 - a fixed command; the id is validated
                [  # noqa: S607 - journalctl from the host's PATH, as systemd runs it
                    "journalctl",
                    "--quiet",
                    "--no-pager",
                    "--output=cat",
                    f"_SYSTEMD_INVOCATION_ID={invocation}",
                ],
                capture_output=True,
                text=True,
                timeout=JOURNAL_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return found
        found = []
        for line in journal.stdout.splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("component") in ("backup", "offsite"):
                found.append(record)
        if any(e.get("event") in ("backup_failed", "backup_complete") for e in found):
            return found
    return found


def stage_of(service_result: str, events: Sequence[Mapping[str, Any]]) -> str:
    """Where a failed run stopped, in STAGES' vocabulary."""
    if service_result == "success":
        return "none"
    if service_result == "timeout":
        return "timeout"
    failures = [
        e for e in events if e.get("component") == "backup" and e.get("event") == "backup_failed"
    ]
    if not failures:
        return "other"
    last = failures[-1]
    if last.get("stage") in DUMP_STAGES:
        return "dump"
    if last.get("offsite") == "failed":
        reasons = {
            e.get("reason")
            for e in events
            if e.get("component") == "offsite" and e.get("event") == "offsite_failed"
        }
        return "verification" if reasons & VERIFICATION_REASONS else "upload"
    return "other"


# --- the files Alloy reads ------------------------------------------------------


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.3f}"


def _gauge(name: str, help_text: str, value: float, labels: Mapping[str, str] | None = None) -> str:
    label_text = ""
    if labels:
        label_text = "{" + ",".join(f'{key}="{val}"' for key, val in labels.items()) + "}"
    return f"# HELP {name} {help_text}\n# TYPE {name} gauge\n{name}{label_text} {_number(value)}\n"


def offsite_exposition(found: Status, now: float) -> str:
    return "".join(
        [
            _gauge(
                "pathable_backup_offsite_last_verified_dump_timestamp_seconds",
                "When the newest backup that S3 is verified to hold was taken (Unix time; 0: none).",
                found.last_verified_dump,
            ),
            _gauge(
                "pathable_backup_local_last_dump_timestamp_seconds",
                "When the newest backup on this host was taken, copied off it or not (Unix time; 0: none).",
                found.last_local_dump,
            ),
            _gauge(
                "pathable_backup_offsite_verified_dumps",
                "Backups on this host with a receipt for their verified copy in S3.",
                found.verified,
            ),
            _gauge(
                "pathable_backup_offsite_pending_dumps",
                "Backups on this host with a manifest and no receipt; the next run copies them.",
                found.pending,
            ),
            _gauge(
                "pathable_backup_status_refreshed_timestamp_seconds",
                "When this status was last worked out (Unix time).",
                round(now, 3),
            ),
        ]
    )


def run_exposition(stage: str, now: float) -> str:
    if stage not in STAGES:
        raise ValueError(f"not a stage: {stage!r}")
    return _gauge(
        "pathable_backup_last_run_success",
        "1 if the backup unit's last run ended with its copy verified in S3, else 0; "
        "stage is where a failed run stopped (none, dump, upload, verification, timeout, other).",
        1 if stage == "none" else 0,
        {"stage": stage},
    ) + _gauge(
        "pathable_backup_last_run_end_timestamp_seconds",
        "When the backup unit's last run ended (Unix time).",
        round(now, 3),
    )


def write(directory: Path, name: str, text: str) -> None:
    # 0644: Alloy reads it as its own user. Nothing in it is secret.
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    offsite.write_atomic(directory / name, text.encode("ascii"), mode=0o644)


# --- commands -------------------------------------------------------------------


@dataclass(frozen=True)
class Places:
    metrics: Path
    backups: Path

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> Places:
        root = Path(environ.get("PATHABLE_ROOT", "/srv/pathable"))
        return cls(
            metrics=Path(environ.get("PATHABLE_METRICS_DIR") or root / "metrics"),
            backups=Path(environ.get("PATHABLE_BACKUP_DIR", "/var/backups/pathable")),
        )


def refresh(places: Places, now: float) -> Status:
    found = status(places.backups)
    write(places.metrics, OFFSITE_FILE, offsite_exposition(found, now))
    emit(
        "status_refreshed",
        verified=found.verified,
        pending=found.pending,
        unrecognised_receipts=found.unrecognised_receipts,
        newest_verified_age_hours=(
            round((now - found.last_verified_dump) / 3600, 2) if found.last_verified_dump else None
        ),
    )
    return found


def record_run(places: Places, environ: Mapping[str, str], now: float) -> int:
    result = environ.get("SERVICE_RESULT")
    if result is None:
        emit(
            "run_not_recorded",
            reason="SERVICE_RESULT is not set: record-run is the backup unit's ExecStopPost",
        )
        return 2
    stage = (
        "none"
        if result == "success"
        else stage_of(result, run_events(environ.get("INVOCATION_ID")))
    )
    write(places.metrics, RUN_FILE, run_exposition(stage, now))
    exit_status = environ.get("EXIT_STATUS", "")
    emit(
        "run_recorded",
        service_result=result if SYSTEMD_WORD.fullmatch(result) else "unrecognised",
        exit_status=exit_status if EXIT_STATUS.fullmatch(exit_status) else None,
        stage=stage,
        success=int(stage == "none"),
    )
    refresh(places, now)
    return 0


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("record-run")
    commands.add_parser("refresh")
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    places = Places.from_environment(env)
    try:
        if args.command == "record-run":
            return record_run(places, env, time.time())
        refresh(places, time.time())
    except OSError as error:
        # The reason, never the path: the status is unreadable either way, and
        # the alerts treat a status that stops arriving as a failure of its own.
        emit("status_not_written", reason=error.strerror or type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
