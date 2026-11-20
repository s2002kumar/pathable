"""A stand-in `docker` for the backup tooling's tests (test_offsite_backup.py).

It answers the commands backup.sh and offsite.py send, and those deploy.sh and
restore.sh send around their backups, from a state directory
($FAKE_DOCKER_STATE), and logs every one of them to calls.jsonl:

* ``docker exec … pg_dump`` writes dump.bin; ``pg_dump --version`` answers 17.5
* ``docker exec -i … pg_restore --list`` lists twelve tables with data
* ``docker exec -i … psql`` answers with db.json, whatever SQL it is sent;
  ``psql … -c "select version_num from alembic_version"`` with its revision
* ``docker inspect --format '{{.State.Running}}' …`` says the container runs
* ``docker compose …``, ``docker build``, ``docker stop`` and ``docker run`` of
  any other image succeed, doing nothing
* ``docker run … amazon/aws-cli:… <args>`` behaves as S3 would for the requests
  offsite.py makes, against s3/: a PUT carrying a SHA-256 is refused if the
  bytes do not hash to it, If-None-Match refuses an existing name, HEAD and GET
  report the stored checksum. It refuses to run without the credentials mount,
  and refuses to read or write a file the container was not given.

$FAKE_S3_FAULTS (comma-separated) breaks it on purpose: deny-put, flaky-put=N,
corrupt-head, corrupt-download, no-sse, psql-fails, and stop-fails (so a
production restore under test goes no further than stopping the API).
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any

STATE = Path(os.environ["FAKE_DOCKER_STATE"])
FAULTS = {f for f in os.environ.get("FAKE_S3_FAULTS", "").split(",") if f}
CREDENTIALS_TARGET = "/run/offsite/credentials"
TAKES_VALUE = {
    "--tmpfs",
    "--cap-drop",
    "--security-opt",
    "--user",
    "-u",
    "--memory",
    "--pids-limit",
    "--mount",
    "--env",
    "-e",
    "-v",
    "--network",
    "--name",
    "--entrypoint",
    "--env-file",
    "-w",
    "--workdir",
}


def out(text: str) -> None:
    sys.stdout.write(text)


def fail(code: int, message: str) -> int:
    sys.stderr.write(message + "\n")
    return code


def aws_error(code: str, operation: str, message: str) -> int:
    return fail(
        254, f"\nAn error occurred ({code}) when calling the {operation} operation: {message}"
    )


def fault(name: str) -> str | None:
    for item in FAULTS:
        if item == name or item.startswith(name + "="):
            return item
    return None


def counter(name: str) -> int:
    path = STATE / "counters.json"
    counts: dict[str, int] = json.loads(path.read_text()) if path.exists() else {}
    counts[name] = counts.get(name, 0) + 1
    path.write_text(json.dumps(counts))
    return counts[name]


def option(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args else None


def split_run(argv: list[str]) -> tuple[list[str], str, list[str]]:
    """`docker run` options, the image, and the command after it."""
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in TAKES_VALUE:
            index += 2
        elif arg.startswith("-"):
            index += 1
        else:
            return argv[:index], arg, argv[index + 1 :]
    raise SystemExit(fail(125, "no image"))


def mounts_of(options: list[str]) -> list[dict[str, str]]:
    found = []
    for index, arg in enumerate(options):
        if arg == "--mount":
            spec: dict[str, str] = {}
            for part in options[index + 1].split(","):
                name, _, value = part.partition("=")
                spec[name] = value if value else "true"
            found.append(spec)
    return found


def covered(path: str, mounts: list[dict[str, str]], writable: bool) -> bool:
    for mount in mounts:
        target = mount.get("target", "")
        if (path == target or path.startswith(target.rstrip("/") + "/")) and not (
            writable and "readonly" in mount
        ):
            return mount.get("source") == target
    return False


# --- S3 -------------------------------------------------------------------------


def object_path(key: str) -> Path:
    return STATE / "s3" / urllib.parse.quote(key, safe="")


def meta_path(key: str) -> Path:
    path = object_path(key)
    return path.with_name(path.name + ".meta.json")


def load_meta(key: str) -> dict[str, Any] | None:
    meta = meta_path(key)
    if not meta.exists():
        return None
    loaded: dict[str, Any] = json.loads(meta.read_text())
    return loaded


def sha256_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def s3(args: list[str], mounts: list[dict[str, str]]) -> int:
    if args[:2] == ["sts", "get-caller-identity"]:
        identity = {
            "UserId": "AIDATESTONLY",
            "Account": "000000000000",
            "Arn": "arn:aws:iam::000000000000:user/pathable-backup-vps",
        }
        out(json.dumps(identity))
        return 0
    if args[0] != "s3api":
        return fail(252, f"unsupported: {args[:2]}")
    operation = args[1]
    key = option(args, "--key") or ""
    (STATE / "s3").mkdir(exist_ok=True)

    if operation == "put-object":
        if fault("deny-put"):
            return aws_error("AccessDenied", "PutObject", "Access Denied")
        flaky = fault("flaky-put")
        if flaky and counter("put") <= int(flaky.split("=")[1]):
            return fail(255, "\nCould not connect to the endpoint URL")
        body = option(args, "--body") or ""
        if not covered(body, mounts, writable=False):
            return fail(255, f"[Errno 2] No such file or directory: '{body}'")
        data = Path(body).read_bytes()
        claimed = option(args, "--checksum-sha256")
        if claimed is not None and claimed != sha256_b64(data):
            return aws_error("BadDigest", "PutObject", "The SHA256 you specified did not match")
        if option(args, "--if-none-match") == "*" and load_meta(key) is not None:
            return aws_error(
                "PreconditionFailed",
                "PutObject",
                "At least one of the pre-conditions you specified did not hold",
            )
        metadata: dict[str, str] = {}
        if (pair := option(args, "--metadata")) is not None:
            name, value = pair.split("=", 1)
            metadata[name] = value
        meta: dict[str, Any] = {
            "ContentLength": len(data),
            "ContentType": option(args, "--content-type"),
            "ETag": '"' + hashlib.md5(data, usedforsecurity=False).hexdigest() + '"',
            "VersionId": f"v{counter('version')}",
            "LastModified": dt.datetime.now(dt.UTC).isoformat(),
            "Metadata": metadata,
        }
        if claimed is not None:
            meta["ChecksumSHA256"] = claimed
            meta["ChecksumType"] = "FULL_OBJECT"
        if option(args, "--server-side-encryption") and not fault("no-sse"):
            meta["ServerSideEncryption"] = option(args, "--server-side-encryption")
        object_path(key).write_bytes(data)
        meta_path(key).write_text(json.dumps(meta))
        answer = {k: meta[k] for k in ("ETag", "VersionId") if k in meta}
        answer.update({k: meta[k] for k in ("ChecksumSHA256", "ServerSideEncryption") if k in meta})
        out(json.dumps(answer))
        return 0

    if operation == "head-object":
        stored = load_meta(key)
        if stored is None:
            return aws_error("404", "HeadObject", "Not Found")
        if fault("corrupt-head") and "ChecksumSHA256" in stored:
            stored["ChecksumSHA256"] = sha256_b64(b"not these bytes")
        out(json.dumps(stored))
        return 0

    if operation == "get-object":
        stored = load_meta(key)
        if stored is None:
            return aws_error("NoSuchKey", "GetObject", "The specified key does not exist.")
        target = args[-1]
        if not covered(target, mounts, writable=True):
            return fail(255, f"[Errno 30] Read-only file system: '{target}'")
        data = object_path(key).read_bytes()
        if fault("corrupt-download"):
            data = data[:-1] + bytes([data[-1] ^ 0xFF])
        Path(target).write_bytes(data)
        out(json.dumps(stored))
        return 0

    if operation == "list-objects-v2":
        prefix = option(args, "--prefix") or ""
        contents = []
        for meta_file in sorted((STATE / "s3").glob("*.meta.json")):
            name = urllib.parse.unquote(meta_file.name[: -len(".meta.json")])
            if name.startswith(prefix):
                stored = json.loads(meta_file.read_text())
                contents.append(
                    {
                        "Key": name,
                        "Size": stored["ContentLength"],
                        "LastModified": stored["LastModified"],
                    }
                )
        out(json.dumps({"Contents": contents} if contents else {"RequestCharged": None}))
        return 0

    return fail(252, f"unsupported operation {operation}")


# --- docker ---------------------------------------------------------------------


def docker_exec(argv: list[str]) -> int:
    index = 0
    while argv[index].startswith("-"):
        index += 2 if argv[index] in TAKES_VALUE else 1
    interactive = "-i" in argv[:index]
    command = argv[index + 1 :]
    if command[0] == "pg_dump":
        if "--version" in command:
            out("pg_dump (PostgreSQL) 17.5 (Debian 17.5-1.pgdg110+1)\n")
            return 0
        sys.stdout.buffer.write((STATE / "dump.bin").read_bytes())
        return 0
    if command[0] == "pg_restore" and "--list" in command:
        sys.stdin.buffer.read()
        out("".join(f"{n}; 0 {n} TABLE DATA public t{n} postgres\n" for n in range(12)))
        return 0
    if command[0] == "psql":
        # Without -i, docker gives the command no standard input.
        if interactive:
            sys.stdin.buffer.read()
        if fault("psql-fails"):
            return fail(2, 'psql: error: relation "alembic_version" does not exist')
        database = (STATE / "db.json").read_text().strip()
        query = option(command, "-c")
        if query is not None and "alembic_version" in query:
            out(json.loads(database)["alembic_revision"] + "\n")
            return 0
        out(database + "\n")
        return 0
    return 0


def main(argv: list[str]) -> int:
    with (STATE / "calls.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(argv) + "\n")
    if argv[0] == "exec":
        return docker_exec(argv[1:])
    if argv[0] == "inspect" and option(argv, "--format") == "{{.State.Running}}":
        out("true\n")
        return 0
    if argv[0] == "stop" and fault("stop-fails"):
        return fail(1, f"Error response from daemon: cannot stop container {argv[-1]}")
    if argv[0] == "run":
        options, image, command = split_run(argv[1:])
        if not image.startswith("amazon/aws-cli:"):
            return 0
        mounts = mounts_of(options)
        credentials = [m for m in mounts if m.get("target") == CREDENTIALS_TARGET]
        env = [options[i + 1] for i, a in enumerate(options) if a == "--env"]
        if (
            not credentials
            or "readonly" not in credentials[0]
            or not Path(credentials[0]["source"]).is_file()
            or f"AWS_SHARED_CREDENTIALS_FILE={CREDENTIALS_TARGET}" not in env
        ):
            return fail(
                253, "\nUnable to locate credentials. You can configure credentials by running"
            )
        if command[-2:] == ["--output", "json"]:
            command = command[:-2]
        return s3(command, mounts)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
