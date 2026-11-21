#!/usr/bin/env python3
"""Off-host copies of this host's database backups, in Amazon S3.

    offsite.py install-credentials [--no-check]
        At a terminal, as the user the backup timer runs as: asks for the
        backup IAM user's access key ID and secret access key without echoing
        either, and writes them to secrets/aws-backup.credentials (mode 0600,
        this user). Nothing is read from the command line or the environment.
    offsite.py check
        Which identity the credentials are, and that the backup prefix lists.
        Writes nothing.
    offsite.py manifest DUMP --label L --started ISO --dump-seconds S [--container C]
        Write DUMP.manifest.json: the file's size and SHA-256, the release that
        was serving, and what the database held (Alembic revision, the active
        datasets with their recorded checksums, the place index, every table's
        row count).
    offsite.py push DUMP
        Upload DUMP and its manifest, then prove S3 holds exactly those bytes.
        DUMP.offsite.json is written only once both objects are verified.
    offsite.py push-pending DIR
        `push` every dump in DIR that has a manifest but no receipt.
    offsite.py list
    offsite.py fetch NAME|latest DIR
        Download a dump and its manifest into DIR, which must not already hold
        them; check S3's checksum and the manifest's SHA-256, and write the
        .sha256 file restore.sh checks.
    offsite.py verify-restore MANIFEST --container C
        Compare a restored database with what its manifest says the original
        held. Used by restore.sh.

Configuration, not secret: ROOT/offsite.env sets PATHABLE_OFFSITE_BUCKET,
PATHABLE_OFFSITE_REGION and PATHABLE_OFFSITE_PREFIX. The credentials are
ROOT/secrets/aws-backup.credentials. ROOT is PATHABLE_ROOT, or /srv/pathable.

Every request runs the pinned AWS CLI image below as this user, with the
credentials file mounted read-only: they never appear in a command line, an
environment variable or a log. Uploads are single PUTs that carry the file's
SHA-256, so S3 refuses bytes that do not hash to it, with SSE-S3 encryption,
and with If-None-Match, so an existing object is never replaced. The bucket is
versioned with a 7-day expiry of old versions, so a replacement would quietly
destroy the earlier backup a week later. Nothing here deletes anything in S3:
retention is the bucket's lifecycle rule.

Output is one JSON object per line.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import getpass
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Pinned by digest. 2.39.3 was published on 2026-10-30.
AWS_CLI_IMAGE = (
    "amazon/aws-cli:2.39.3@sha256:d557b3524475d70dd3104cf44d96568ee3188897b73a6ece2b37aa50e1af362f"
)
CREDENTIALS_NAME = "aws-backup.credentials"
CREDENTIALS_IN_CONTAINER = "/run/offsite/credentials"
MANIFEST_FORMAT = "pathable-backup-manifest/1"
RECEIPT_FORMAT = "pathable-offsite-receipt/1"
MANIFEST_SUFFIX = ".manifest.json"
RECEIPT_SUFFIX = ".offsite.json"
#: The two ways S3 can answer and still not hold a backup. backup_metrics.py
#: reports a run that stopped on either as a verification failure, not an upload.
NAME_TAKEN = "a different object already has this name; refusing to replace it"
NOT_HELD = "remote verification failed: S3 does not hold these bytes"
#: S3's limit for one PUT. Above it the upload would have to be multipart,
#: whose checksum is no longer the file's own SHA-256.
SINGLE_PUT_LIMIT = 5 * 1024**3
ATTEMPTS = 3
BACKOFF_SECONDS = (10.0, 30.0)
AWS_TIMEOUT_SECONDS = 1800
DB_TIMEOUT_SECONDS = 300

DUMP_NAME = re.compile(r"pathable-\d{8}T\d{6}Z-[a-z0-9-]+-(?:[0-9a-f]{12}|unknown)\.dump")
ACCESS_KEY_ID = re.compile(r"AKIA[A-Z0-9]{16}")
SECRET_ACCESS_KEY = re.compile(r"[A-Za-z0-9/+=]{40}")
KEY_ID_SHAPED = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
BUCKET_NAME = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
REGION_NAME = re.compile(r"[a-z]{2}(?:-[a-z]+)+-\d")
PREFIX = re.compile(r"(?:[A-Za-z0-9!_.*'()-]+/)+")
AWS_ERROR = re.compile(r"An error occurred \(([^)]+)\)")
#: Retrying any of these cannot succeed.
PERMANENT_ERRORS = frozenset(
    {
        "AccessDenied",
        "AllAccessDisabled",
        "AuthorizationHeaderMalformed",
        "InvalidAccessKeyId",
        "InvalidArgument",
        "InvalidBucketName",
        "InvalidRequest",
        "NoSuchBucket",
        "PermanentRedirect",
        "SignatureDoesNotMatch",
    }
)

#: What a database held, read the same way from the original (into the
#: manifest) and from a restored copy (by verify-restore). Row counts are exact
#: and cover every table in `public` except those an extension owns.
DATABASE_CONTEXT_SQL = """
select json_build_object(
    'alembic_revision', (select version_num from alembic_version),
    'server_version', current_setting('server_version'),
    'database_bytes', pg_database_size(current_database()),
    'active_datasets', coalesce((
        select json_agg(json_build_object(
                'id', id,
                'source_type', source_type,
                'pilot_region_id', pilot_region_id,
                'checksum', checksum,
                'content_checksum', content_checksum,
                'content_checksum_version', content_checksum_version,
                'node_count', node_count,
                'edge_count', edge_count
            ) order by created_at)
        from dataset_versions where status = 'active'
    ), '[]'::json),
    'place_index', json_build_object(
        'entries', (select count(*) from gazetteer_entries),
        'builds', (select count(*) from gazetteer_builds)
    ),
    'table_rows', (
        select json_object_agg(
            c.relname,
            (xpath('/row/n/text()', query_to_xml(
                format('select count(*) as n from public.%I', c.relname), false, true, ''
            )))[1]::text::bigint
            order by c.relname
        )
        from pg_class c
        join pg_namespace s on s.oid = c.relnamespace
        where s.nspname = 'public'
          and c.relkind in ('r', 'p')
          and not exists (
              select 1 from pg_depend d
              where d.classid = 'pg_class'::regclass and d.objid = c.oid and d.deptype = 'e'
          )
    )
);
"""

# --- output -------------------------------------------------------------------


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def emit(event: str, **fields: Any) -> None:
    record = {"ts": utc_now(), "component": "offsite", "event": event, **fields}
    print(json.dumps(record, separators=(",", ":")), flush=True)


class OffsiteError(Exception):
    """A step failed; `fields` say which and why, and never hold a credential."""

    def __init__(self, reason: str, **fields: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.fields = fields


class AwsError(Exception):
    def __init__(self, exit_code: int, detail: str) -> None:
        super().__init__(detail)
        self.exit_code = exit_code
        match = AWS_ERROR.search(detail)
        self.code = match.group(1) if match else None
        # Never let an access key ID reach a log, even inside an AWS message.
        self.detail = KEY_ID_SHAPED.sub("<access-key-id>", detail.strip())[-400:]

    @property
    def label(self) -> str:
        return self.code or f"exit {self.exit_code}"

    @property
    def permanent(self) -> bool:
        # 252: the CLI rejected its arguments; 253: its configuration.
        return self.code in PERMANENT_ERRORS or self.exit_code in (252, 253)


# --- files --------------------------------------------------------------------


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def b64_of(hex_digest: str) -> str:
    return base64.b64encode(bytes.fromhex(hex_digest)).decode("ascii")


def hex_of(b64_digest: str | None) -> str | None:
    if not b64_digest:
        return None
    try:
        return base64.b64decode(b64_digest, validate=True).hex()
    except ValueError:
        return None


def write_atomic(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Write `data` to `path` (mode 0600 unless told), so a reader sees all of it or none."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def read_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise OffsiteError("no manifest", path=str(path)) from error
    except ValueError as error:
        raise OffsiteError("the manifest is not JSON", path=str(path)) from error
    if not isinstance(manifest, dict) or manifest.get("format") != MANIFEST_FORMAT:
        raise OffsiteError("not a backup manifest", path=str(path))
    return manifest


# --- configuration ------------------------------------------------------------


@dataclass(frozen=True)
class Paths:
    root: Path

    @classmethod
    def from_environment(cls) -> Paths:
        return cls(Path(os.environ.get("PATHABLE_ROOT", "/srv/pathable")))

    @property
    def config(self) -> Path:
        return self.root / "offsite.env"

    @property
    def secrets(self) -> Path:
        return self.root / "secrets"

    @property
    def credentials(self) -> Path:
        return self.secrets / CREDENTIALS_NAME

    @property
    def release(self) -> Path:
        return self.root / "release.env"


@dataclass(frozen=True)
class Config:
    bucket: str
    region: str
    prefix: str
    credentials: Path


def load_config(paths: Paths) -> Config:
    if not paths.config.is_file():
        raise OffsiteError("off-host copies are not configured", path=str(paths.config))
    values = read_env_file(paths.config)
    bucket = values.get("PATHABLE_OFFSITE_BUCKET", "")
    region = values.get("PATHABLE_OFFSITE_REGION", "")
    prefix = values.get("PATHABLE_OFFSITE_PREFIX", "")
    if not BUCKET_NAME.fullmatch(bucket):
        raise OffsiteError("PATHABLE_OFFSITE_BUCKET is missing or not a bucket name")
    if not REGION_NAME.fullmatch(region):
        raise OffsiteError("PATHABLE_OFFSITE_REGION is missing or not a region")
    if not PREFIX.fullmatch(prefix):
        raise OffsiteError("PATHABLE_OFFSITE_PREFIX must be like backups/ (ending in /)")
    credentials = paths.credentials
    try:
        mode = credentials.stat().st_mode
    except FileNotFoundError as error:
        raise OffsiteError(
            "no credentials; run offsite.py install-credentials", path=str(credentials)
        ) from error
    # Only the metadata is read here, never the contents.
    if mode & 0o077:
        raise OffsiteError(
            "the credentials file can be read by others; it must be mode 0600",
            path=str(credentials),
            mode=oct(stat.S_IMODE(mode)),
        )
    return Config(bucket=bucket, region=region, prefix=prefix, credentials=credentials)


# --- the AWS CLI --------------------------------------------------------------


def aws_argv(
    config: Config, args: Sequence[str], mounts: Sequence[tuple[Path, bool]] = ()
) -> list[str]:
    """`docker run` of the pinned AWS CLI, as this user, for one request.

    The credentials file is bind-mounted read-only; the only thing about it on
    the command line is where it is. Files the request reads or writes are
    mounted at their own paths, read-only unless the request writes them.
    """
    argv = [
        "docker", "run", "--rm",
        "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--memory", "512m", "--pids-limit", "256",
        "--mount", f"type=bind,source={config.credentials},target={CREDENTIALS_IN_CONTAINER},readonly",
        "--env", f"AWS_SHARED_CREDENTIALS_FILE={CREDENTIALS_IN_CONTAINER}",
        "--env", "AWS_CONFIG_FILE=/run/offsite/no-config-file",
        "--env", f"AWS_REGION={config.region}",
        "--env", "AWS_RETRY_MODE=standard",
        "--env", "AWS_MAX_ATTEMPTS=5",
        "--env", "AWS_EC2_METADATA_DISABLED=true",
        "--env", "AWS_PAGER=",
        "--env", "HOME=/tmp",
    ]  # fmt: skip
    for path, writable in mounts:
        argv += [
            "--mount",
            f"type=bind,source={path},target={path}" + ("" if writable else ",readonly"),
        ]
    return [*argv, AWS_CLI_IMAGE, *args, "--output", "json"]


def aws(
    config: Config, args: Sequence[str], mounts: Sequence[tuple[Path, bool]] = ()
) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            aws_argv(config, args, mounts),
            capture_output=True,
            check=False,
            timeout=AWS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise AwsError(124, f"no answer within {AWS_TIMEOUT_SECONDS}s") from error
    if proc.returncode != 0:
        raise AwsError(proc.returncode, proc.stderr.decode("utf-8", "replace"))
    out = proc.stdout.decode("utf-8").strip()
    parsed: dict[str, Any] = json.loads(out) if out else {}
    return parsed


def with_retries[T](operation: str, target: str, call: Callable[[], T]) -> T:
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return call()
        except AwsError as error:
            if error.permanent or attempt == ATTEMPTS:
                raise
            delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS)) - 1]
            emit(
                "retry",
                operation=operation,
                object=target,
                attempt=attempt,
                error=error.label,
                retry_in_s=delay,
            )
            time.sleep(delay)
    raise AssertionError("unreachable")


def head(config: Config, name: str) -> dict[str, Any] | None:
    """The object's metadata with its stored checksum, or None if absent."""

    def call() -> dict[str, Any] | None:
        try:
            return aws(
                config,
                [
                    "s3api",
                    "head-object",
                    "--bucket",
                    config.bucket,
                    "--key",
                    name,
                    "--checksum-mode",
                    "ENABLED",
                ],
            )
        except AwsError as error:
            if error.code in ("404", "NotFound", "NoSuchKey"):
                return None
            raise

    return with_retries("head-object", name, call)


def list_objects(config: Config) -> list[dict[str, Any]]:
    listing = with_retries(
        "list-objects-v2",
        config.prefix,
        lambda: aws(
            config,
            ["s3api", "list-objects-v2", "--bucket", config.bucket, "--prefix", config.prefix],
        ),
    )
    contents: list[dict[str, Any]] = listing.get("Contents") or []
    return contents


def holds(meta: dict[str, Any], size: int, b64_digest: str) -> bool:
    """Whether S3's own record of an object is exactly these bytes, encrypted."""
    return (
        meta.get("ContentLength") == size
        and meta.get("ChecksumSHA256") == b64_digest
        and meta.get("ChecksumType", "FULL_OBJECT") == "FULL_OBJECT"
        and meta.get("ServerSideEncryption") == "AES256"
    )


def described(meta: dict[str, Any] | None) -> dict[str, Any]:
    if meta is None:
        return {"s3": "absent"}
    return {
        "s3_bytes": meta.get("ContentLength"),
        "s3_sha256": hex_of(meta.get("ChecksumSHA256")),
        "s3_encryption": meta.get("ServerSideEncryption"),
    }


# --- push ---------------------------------------------------------------------


def put_verified(config: Config, path: Path, content_type: str) -> dict[str, Any]:
    """Upload one file unless S3 already holds it; either way, prove it does."""
    digest = file_sha256(path)
    checksum = b64_of(digest)
    size = path.stat().st_size
    name = config.prefix + path.name
    if size > SINGLE_PUT_LIMIT:
        raise OffsiteError(
            "too large for one PUT; uploads would need multipart", object=name, bytes=size
        )

    existing = head(config, name)
    uploaded = False
    seconds: float | None = None
    if existing is not None:
        if not holds(existing, size, checksum):
            raise OffsiteError(
                NAME_TAKEN,
                object=name,
                bytes=size,
                sha256=digest,
                **described(existing),
            )
        emit(
            "already_present",
            object=name,
            bytes=size,
            sha256=digest,
            version_id=existing.get("VersionId"),
        )
    else:
        started = time.monotonic()

        request = [
            "s3api", "put-object",
            "--bucket", config.bucket, "--key", name, "--body", str(path),
            "--content-type", content_type,
            "--server-side-encryption", "AES256",
            "--checksum-algorithm", "SHA256", "--checksum-sha256", checksum,
            "--if-none-match", "*",
            "--metadata", f"sha256={digest}",
        ]  # fmt: skip

        def put() -> bool:
            try:
                aws(config, request, mounts=[(path, False)])
            except AwsError as error:
                # An earlier attempt reached S3 after all; the check below
                # decides whether what it stored is these bytes.
                if error.code == "PreconditionFailed":
                    return False
                raise
            return True

        try:
            uploaded = with_retries("put-object", name, put)
        except AwsError as error:
            raise OffsiteError(
                "upload failed", object=name, error=error.label, detail=error.detail
            ) from error
        seconds = round(time.monotonic() - started, 2)
        emit(
            "uploaded" if uploaded else "upload_already_landed",
            object=name,
            bytes=size,
            sha256=digest,
            seconds=seconds,
        )

    verified = head(config, name)
    if verified is None or not holds(verified, size, checksum):
        raise OffsiteError(
            NOT_HELD,
            object=name,
            bytes=size,
            sha256=digest,
            **described(verified),
        )
    emit("verified", object=name, bytes=size, sha256=digest, version_id=verified.get("VersionId"))
    return {
        "object": name,
        "bytes": size,
        "sha256": digest,
        "version_id": verified.get("VersionId"),
        "etag": verified.get("ETag"),
        "encryption": verified.get("ServerSideEncryption"),
        "uploaded": uploaded,
        "put_seconds": seconds,
    }


def push(paths: Paths, dump: Path) -> dict[str, Any]:
    config = load_config(paths)
    dump = dump.resolve()
    if not dump.is_file():
        raise OffsiteError("no such dump", path=str(dump))
    manifest_path = dump.with_name(dump.name + MANIFEST_SUFFIX)
    manifest = read_manifest(manifest_path)
    digest = file_sha256(dump)
    size = dump.stat().st_size
    if (
        manifest.get("file") != dump.name
        or manifest.get("sha256") != digest
        or manifest.get("bytes") != size
    ):
        raise OffsiteError(
            "the manifest does not describe this file",
            file=dump.name,
            bytes=size,
            sha256=digest,
            manifest_file=manifest.get("file"),
            manifest_bytes=manifest.get("bytes"),
            manifest_sha256=manifest.get("sha256"),
        )
    started = time.monotonic()
    # The dump first: a manifest in S3 means its dump is already there.
    objects = [
        put_verified(config, dump, "application/octet-stream"),
        put_verified(config, manifest_path, "application/json"),
    ]
    receipt = {
        "format": RECEIPT_FORMAT,
        "file": dump.name,
        "bucket": config.bucket,
        "region": config.region,
        "objects": objects,
        "verified_at": utc_now(),
        "seconds": round(time.monotonic() - started, 2),
    }
    write_atomic(
        dump.with_name(dump.name + RECEIPT_SUFFIX), (json.dumps(receipt, indent=2) + "\n").encode()
    )
    emit(
        "offsite_copy_verified",
        file=dump.name,
        objects=[o["object"] for o in objects],
        bytes=sum(o["bytes"] for o in objects),
        seconds=receipt["seconds"],
    )
    return receipt


def push_pending(paths: Paths, directory: Path) -> int:
    pending = [
        dump
        for dump in sorted(directory.glob("pathable-*.dump"))
        if dump.with_name(dump.name + MANIFEST_SUFFIX).is_file()
        and not dump.with_name(dump.name + RECEIPT_SUFFIX).exists()
    ]
    failed = 0
    for dump in pending:
        try:
            push(paths, dump)
        except OffsiteError as error:
            failed += 1
            emit("offsite_failed", file=dump.name, reason=error.reason, **error.fields)
    emit("pending_pushed", pending=len(pending), failed=failed)
    return 1 if failed else 0


# --- fetch --------------------------------------------------------------------


def get_verified(
    config: Config,
    filename: str,
    directory: Path,
    expected_sha256: str | None = None,
    expected_bytes: int | None = None,
) -> dict[str, Any]:
    name = config.prefix + filename
    partial = directory / f"{filename}.partial"
    partial.unlink(missing_ok=True)
    started = time.monotonic()
    try:
        request = [
            "s3api", "get-object", "--bucket", config.bucket, "--key", name,
            "--checksum-mode", "ENABLED", str(partial),
        ]  # fmt: skip
        response = with_retries(
            "get-object", name, lambda: aws(config, request, mounts=[(directory, True)])
        )
    except AwsError as error:
        partial.unlink(missing_ok=True)
        raise OffsiteError(
            "download failed", object=name, error=error.label, detail=error.detail
        ) from error
    seconds = round(time.monotonic() - started, 2)
    digest = file_sha256(partial)
    size = partial.stat().st_size
    problems = []
    if response.get("ChecksumSHA256") != b64_of(digest):
        problems.append("the bytes received do not hash to the SHA-256 S3 stored")
    if expected_sha256 is not None and digest != expected_sha256:
        problems.append("the bytes received do not hash to the manifest's SHA-256")
    if expected_bytes is not None and size != expected_bytes:
        problems.append("the size differs from the manifest's")
    if problems:
        partial.unlink(missing_ok=True)
        raise OffsiteError(
            "download failed verification",
            object=name,
            problems=problems,
            bytes=size,
            sha256=digest,
            s3_sha256=hex_of(response.get("ChecksumSHA256")),
        )
    final = directory / filename
    partial.chmod(0o600)
    partial.replace(final)
    emit(
        "downloaded",
        object=name,
        bytes=size,
        sha256=digest,
        version_id=response.get("VersionId"),
        seconds=seconds,
    )
    return {
        "object": name,
        "bytes": size,
        "sha256": digest,
        "version_id": response.get("VersionId"),
        "seconds": seconds,
    }


def latest_dump(config: Config) -> str:
    names = sorted(
        obj["Key"][len(config.prefix) : -len(MANIFEST_SUFFIX)]
        for obj in list_objects(config)
        if obj.get("Key", "").endswith(MANIFEST_SUFFIX)
        and DUMP_NAME.fullmatch(obj["Key"][len(config.prefix) : -len(MANIFEST_SUFFIX)])
    )
    if not names:
        raise OffsiteError("no backup with a manifest under the prefix", prefix=config.prefix)
    # The names begin with the UTC time they were taken, so they sort by age.
    return names[-1]


def fetch(paths: Paths, which: str, directory: Path) -> dict[str, Any]:
    config = load_config(paths)
    name = latest_dump(config) if which == "latest" else which
    if not DUMP_NAME.fullmatch(name):
        raise OffsiteError("not a backup file name", name=name)
    directory = directory.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    targets = [
        directory / name,
        directory / (name + MANIFEST_SUFFIX),
        directory / (name + ".sha256"),
    ]
    present = [str(t) for t in targets if t.exists()]
    if present:
        raise OffsiteError("refusing to overwrite files already in the directory", present=present)
    started = time.monotonic()
    manifest_copy = get_verified(config, name + MANIFEST_SUFFIX, directory)
    try:
        manifest = read_manifest(directory / (name + MANIFEST_SUFFIX))
        if manifest.get("file") != name:
            raise OffsiteError(
                "the manifest describes another file", name=name, manifest_file=manifest.get("file")
            )
        dump_copy = get_verified(
            config,
            name,
            directory,
            expected_sha256=manifest.get("sha256"),
            expected_bytes=manifest.get("bytes"),
        )
    except OffsiteError:
        # Leave nothing that would make a retry refuse, or look like a backup.
        (directory / (name + MANIFEST_SUFFIX)).unlink(missing_ok=True)
        raise
    write_atomic(directory / (name + ".sha256"), f"{dump_copy['sha256']}  {name}\n".encode())
    result = {
        "file": name,
        "directory": str(directory),
        "objects": [dump_copy, manifest_copy],
        "seconds": round(time.monotonic() - started, 2),
    }
    emit(
        "fetched",
        file=name,
        directory=str(directory),
        bytes=dump_copy["bytes"],
        sha256=dump_copy["sha256"],
        seconds=result["seconds"],
    )
    return result


# --- the database ---------------------------------------------------------------


def database_context(container: str) -> dict[str, Any]:
    try:
        psql = [
            "docker", "exec", "-i", container,
            "psql", "-X", "-v", "ON_ERROR_STOP=1", "-At", "-U", "postgres", "-d", "pathable",
        ]  # fmt: skip
        proc = subprocess.run(
            psql,
            input=DATABASE_CONTEXT_SQL.encode(),
            capture_output=True,
            check=False,
            timeout=DB_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise OffsiteError("the database did not answer", container=container) from error
    if proc.returncode != 0:
        raise OffsiteError(
            "could not read what the database holds",
            container=container,
            detail=proc.stderr.decode("utf-8", "replace").strip()[-400:],
        )
    context: dict[str, Any] = json.loads(proc.stdout)
    return context


def pg_dump_version(container: str) -> str | None:
    proc = subprocess.run(
        ["docker", "exec", container, "pg_dump", "--version"],
        capture_output=True,
        check=False,
        timeout=60,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.decode().strip() or None


def write_manifest(
    paths: Paths, dump: Path, label: str, started: str, dump_seconds: float, container: str
) -> dict[str, Any]:
    dump = dump.resolve()
    if not DUMP_NAME.fullmatch(dump.name):
        raise OffsiteError("not a backup file name", name=dump.name)
    release = read_env_file(paths.release) if paths.release.is_file() else {}
    database = database_context(container)
    database["queried_at"] = utc_now()
    manifest = {
        "format": MANIFEST_FORMAT,
        "file": dump.name,
        "bytes": dump.stat().st_size,
        "sha256": file_sha256(dump),
        "label": label,
        "dump_started_at": started,
        "dump_seconds": dump_seconds,
        "pg_dump": pg_dump_version(container),
        "release": {
            "revision": release.get("PATHABLE_REVISION"),
            "app_version": release.get("PATHABLE_APP_VERSION"),
            "deployed_at": release.get("PATHABLE_DEPLOYED_AT"),
        },
        "database": database,
        "written_at": utc_now(),
    }
    write_atomic(
        dump.with_name(dump.name + MANIFEST_SUFFIX),
        (json.dumps(manifest, indent=2) + "\n").encode(),
    )
    emit(
        "manifest_written",
        file=dump.name,
        alembic_revision=database.get("alembic_revision"),
        active_datasets=[d.get("id") for d in database.get("active_datasets") or []],
        tables=len(database.get("table_rows") or {}),
    )
    return manifest


def compare_database(expected: dict[str, Any], restored: dict[str, Any]) -> list[str]:
    """Every way a restored database differs from what its manifest recorded."""
    problems = []
    if expected.get("alembic_revision") != restored.get("alembic_revision"):
        problems.append(
            f"alembic revision: recorded {expected.get('alembic_revision')}, restored {restored.get('alembic_revision')}"
        )
    want = expected.get("table_rows") or {}
    got = restored.get("table_rows") or {}
    for table in sorted(set(want) | set(got)):
        if want.get(table) != got.get(table):
            problems.append(
                f"table {table}: recorded {want.get(table, 'absent')} rows, restored {got.get(table, 'absent')}"
            )
    fields = (
        "source_type",
        "checksum",
        "content_checksum",
        "content_checksum_version",
        "node_count",
        "edge_count",
    )
    want_sets = {
        d["id"]: {f: d.get(f) for f in fields} for d in expected.get("active_datasets") or []
    }
    got_sets = {
        d["id"]: {f: d.get(f) for f in fields} for d in restored.get("active_datasets") or []
    }
    for dataset in sorted(set(want_sets) | set(got_sets)):
        if want_sets.get(dataset) != got_sets.get(dataset):
            problems.append(
                f"active dataset {dataset}: recorded {want_sets.get(dataset)}, restored {got_sets.get(dataset)}"
            )
    if (expected.get("place_index") or {}) != (restored.get("place_index") or {}):
        problems.append(
            f"place index: recorded {expected.get('place_index')}, restored {restored.get('place_index')}"
        )
    return problems


def verify_restore(manifest_path: Path, container: str) -> int:
    manifest = read_manifest(manifest_path)
    restored = database_context(container)
    problems = compare_database(manifest.get("database") or {}, restored)
    for problem in problems:
        emit("restore_mismatch", file=manifest.get("file"), detail=problem)
    if problems:
        return 1
    rows = restored.get("table_rows") or {}
    emit(
        "restore_matches_manifest",
        file=manifest.get("file"),
        alembic_revision=restored.get("alembic_revision"),
        tables=len(rows),
        rows=sum(rows.values()),
        active_datasets=[d.get("id") for d in restored.get("active_datasets") or []],
        place_index=restored.get("place_index"),
    )
    return 0


# --- credentials ----------------------------------------------------------------


def install_credentials(paths: Paths, check_after: bool) -> int:
    """Ask for the two keys at the terminal and store them for the backup job.

    Both are read with echo off (getpass, from the controlling terminal); if
    echo cannot be turned off, this stops rather than read them visibly. They
    stay inside this process: no command is ever run with them, they are not
    exported, and no message includes them.
    """
    if os.geteuid() == 0:
        print(
            "Run this as the user the backup timer runs as (ubuntu), not as root.", file=sys.stderr
        )
        return 2
    if not sys.stdin.isatty():
        print("This must be run at an interactive terminal.", file=sys.stderr)
        return 2
    secrets = paths.secrets
    try:
        info = secrets.lstat()
    except FileNotFoundError:
        print(f"{secrets} does not exist.", file=sys.stderr)
        return 2
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        print(f"{secrets} must be a directory owned by you with mode 0700.", file=sys.stderr)
        return 2
    target = paths.credentials
    if target.exists() or target.is_symlink():
        answer = input(f"{target} already exists. Replace it? [y/N] ").strip().lower()
        if answer != "y":
            print("Left unchanged.", file=sys.stderr)
            return 1

    print(
        "Enter the access key of the backup IAM user. Neither value will be shown.", file=sys.stderr
    )
    with warnings.catch_warnings():
        # Without control of the terminal, getpass would read with echo ON.
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            key_id = getpass.getpass("AWS access key ID: ").strip()
            secret = getpass.getpass("AWS secret access key: ").strip()
        except getpass.GetPassWarning:
            print(
                "Could not turn off echo on this terminal; nothing was read or stored.",
                file=sys.stderr,
            )
            return 2
    if key_id.startswith("ASIA"):
        print(
            "That is a temporary key, which needs a session token; use the IAM user's own key.",
            file=sys.stderr,
        )
        return 2
    if not ACCESS_KEY_ID.fullmatch(key_id):
        print(
            "The access key ID is not 20 characters starting AKIA; nothing was stored.",
            file=sys.stderr,
        )
        return 2
    if not SECRET_ACCESS_KEY.fullmatch(secret):
        print(
            "The secret access key is not 40 characters of A-Z a-z 0-9 / + =; nothing was stored.",
            file=sys.stderr,
        )
        return 2
    content = f"[default]\naws_access_key_id = {key_id}\naws_secret_access_key = {secret}\n".encode(
        "ascii"
    )
    del key_id, secret
    write_atomic(target, content)
    del content
    written = target.stat()
    emit(
        "credentials_installed",
        path=str(target),
        mode=oct(stat.S_IMODE(written.st_mode)),
        owner_uid=written.st_uid,
        bytes=written.st_size,
    )
    if not check_after:
        return 0
    if not paths.config.is_file():
        emit("check_skipped", reason="off-host copies are not configured", path=str(paths.config))
        return 0
    return run(lambda: check(paths))


def check(paths: Paths) -> None:
    config = load_config(paths)
    identity = with_retries(
        "get-caller-identity", "-", lambda: aws(config, ["sts", "get-caller-identity"])
    )
    arn = str(identity.get("Arn", ""))
    objects = list_objects(config)
    emit(
        "check_passed",
        # The principal's name, without the account number.
        principal=arn.split(":", 5)[-1] if arn.startswith("arn:") else None,
        region=config.region,
        prefix=config.prefix,
        objects=len(objects),
    )


def list_command(paths: Paths) -> None:
    config = load_config(paths)
    objects = sorted(list_objects(config), key=lambda o: o.get("Key", ""))
    for obj in objects:
        emit(
            "object", object=obj.get("Key"), bytes=obj.get("Size"), modified=obj.get("LastModified")
        )
    emit(
        "listed",
        prefix=config.prefix,
        objects=len(objects),
        bytes=sum(o.get("Size", 0) for o in objects),
    )


# --- entry point ----------------------------------------------------------------


def run(step: Callable[[], object]) -> int:
    try:
        step()
    except OffsiteError as error:
        emit("offsite_failed", reason=error.reason, **error.fields)
        return 1
    except AwsError as error:
        emit("offsite_failed", reason="AWS request failed", error=error.label, detail=error.detail)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install-credentials")
    install.add_argument("--no-check", action="store_true", help="do not call AWS afterwards")
    commands.add_parser("check")
    manifest = commands.add_parser("manifest")
    manifest.add_argument("dump", type=Path)
    manifest.add_argument("--label", required=True)
    manifest.add_argument("--started", required=True)
    manifest.add_argument("--dump-seconds", type=float, required=True)
    manifest.add_argument("--container", default="pathable-db-1")
    push_parser = commands.add_parser("push")
    push_parser.add_argument("dump", type=Path)
    pending = commands.add_parser("push-pending")
    pending.add_argument("directory", type=Path)
    commands.add_parser("list")
    fetch_parser = commands.add_parser("fetch")
    fetch_parser.add_argument("name", help="a backup file name, or latest")
    fetch_parser.add_argument("directory", type=Path)
    verify = commands.add_parser("verify-restore")
    verify.add_argument("manifest", type=Path)
    verify.add_argument("--container", required=True)
    args = parser.parse_args(argv)
    paths = Paths.from_environment()

    if args.command == "install-credentials":
        return install_credentials(paths, check_after=not args.no_check)
    if args.command == "check":
        return run(lambda: check(paths))
    if args.command == "manifest":
        return run(
            lambda: write_manifest(
                paths, args.dump, args.label, args.started, args.dump_seconds, args.container
            )
        )
    if args.command == "push":
        return run(lambda: push(paths, args.dump))
    if args.command == "push-pending":
        return push_pending(paths, args.directory)
    if args.command == "list":
        return run(lambda: list_command(paths))
    if args.command == "fetch":
        return run(lambda: fetch(paths, args.name, args.directory))
    if args.command == "verify-restore":
        try:
            return verify_restore(args.manifest, args.container)
        except OffsiteError as error:
            emit("offsite_failed", reason=error.reason, **error.fields)
            return 1
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
