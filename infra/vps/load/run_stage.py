#!/usr/bin/env python3
"""Run one load stage against the pilot's API, on the pilot host, and watch it.

    python3 run_stage.py --stage c10 --mode route --vus 10 --duration 90s \
        --out /srv/pathable/evidence/load-20261115

Standard library only (the host's own Python 3). Run as the deploy user, who
is in the docker group; ``sudo`` is used once per snapshot, to read Alloy's
loopback-only API from inside its network namespace.

What one stage does, in order:

1. **Refuses to start** unless the API container is running and public
   readiness answers 200.
2. **Snapshots** the API's Prometheus exposition (port 9464 on the edge
   network) and Alloy's remote-write counters, and every container's restart
   count and OOM state.
3. **Samples once a second** each container's cgroup memory and CPU, host
   memory, host CPU including steal, and CPU pressure, into
   ``<stage>-samples.jsonl``; and **probes public readiness** through Caddy and
   TLS every five seconds into ``<stage>-readiness.jsonl``.
4. **Runs k6** (load.js, pinned image) inside the Compose project's edge
   network, so requests reach the production API process directly.
5. **Stops the stage early** — SIGINT to k6, so it still writes its summary —
   if the API's anonymous (unreclaimable) memory passes
   ``--api-memory-abort-mib``, host memory available falls below
   ``--host-available-abort-mib``, or readiness has failed for
   ``--readiness-abort-seconds`` without a break. The last keeps a saturated
   stage well inside the availability alert's two minutes.
6. **Waits for readiness to recover**, records how long it took, snapshots
   again, and writes ``<stage>-run.json``.

Nothing here restarts, reconfigures or redeploys anything.
"""

# ruff: noqa: S310, S603, T201 - an operator script: fixed argv, prints
# its progress, opens only the pilot's own origin and container addresses.

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

#: grafana/k6 1.8.1 (pushed 2026-08-12), pinned by its multi-arch index digest.
K6_IMAGE = (
    "grafana/k6:1.8.1@sha256:23f2279054c3e01535455c4d92914a625df12aeb631ea0e3ea7a43f96bcbc843"
)
K6_CONTAINER = "pathable-load-k6"
NETWORK = "pathable_edge"
API = "pathable-api-1"
ALLOY = "pathable-alloy-1"
WATCHED = (API, "pathable-db-1", "pathable-caddy-1", "pathable-web-1", ALLOY)
METRICS_PORT = 9464
ALLOY_METRICS = "http://127.0.0.1:12345/metrics"
ALLOY_COMPONENTS = "http://127.0.0.1:12345/api/v0/web/components"
#: The Alloy series that say whether telemetry is leaving the host.
ALLOY_SERIES = (
    "prometheus_remote_storage_samples_total",
    "prometheus_remote_storage_samples_failed_total",
    "prometheus_remote_storage_samples_retried_total",
    "prometheus_remote_storage_samples_pending",
    "prometheus_remote_storage_sent_batch_duration_seconds_count",
    "prometheus_remote_write_wal_storage_active_series",
)


def utc(epoch: float | None = None) -> str:
    moment = dt.datetime.fromtimestamp(epoch if epoch is not None else time.time(), tz=dt.UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def run(args: list[str], *, check: bool = True) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        msg = f"{' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        raise RuntimeError(msg)
    return result.stdout


def inspect(container: str, template: str) -> str:
    return run(["docker", "inspect", "--format", template, container], check=False).strip()


def cgroup_dir(container: str) -> Path | None:
    container_id = inspect(container, "{{.Id}}")
    if not container_id:
        return None
    path = Path(f"/sys/fs/cgroup/system.slice/docker-{container_id}.scope")
    return path if path.is_dir() else None


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def keyed(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            values[parts[0]] = int(parts[1])
    return values


def cgroup_sample(path: Path) -> dict[str, int | None]:
    current = read_text(path / "memory.current").strip()
    cpu = keyed(read_text(path / "cpu.stat"))
    stat = keyed(read_text(path / "memory.stat"))
    return {
        "memory_current": int(current) if current.isdigit() else None,
        "anon": stat.get("anon"),
        "file": stat.get("file"),
        "cpu_usage_usec": cpu.get("usage_usec"),
        "cpu_throttled_usec": cpu.get("throttled_usec"),
    }


def host_sample() -> dict[str, Any]:
    meminfo: dict[str, int] = {}
    for line in read_text(Path("/proc/meminfo")).splitlines():
        key, _, rest = line.partition(":")
        if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            meminfo[key] = int(rest.split()[0]) * 1024
    cpu_line = read_text(Path("/proc/stat")).splitlines()[0].split()
    # user nice system idle iowait irq softirq steal, in clock ticks.
    ticks = [int(value) for value in cpu_line[1:9]]
    pressure = read_text(Path("/proc/pressure/cpu")).split()
    some_avg10 = next((p.split("=")[1] for p in pressure if p.startswith("avg10=")), None)
    return {
        "mem_available": meminfo.get("MemAvailable"),
        "mem_total": meminfo.get("MemTotal"),
        "swap_used": (meminfo.get("SwapTotal", 0) - meminfo.get("SwapFree", 0)),
        "cpu_ticks": ticks,
        "cpu_pressure_some_avg10": float(some_avg10) if some_avg10 else None,
        "loadavg_1m": float(read_text(Path("/proc/loadavg")).split()[0]),
    }


def container_state() -> dict[str, Any]:
    state: dict[str, Any] = {}
    for name in WATCHED:
        line = inspect(
            name, "{{.RestartCount}}|{{.State.OOMKilled}}|{{.State.StartedAt}}|{{.State.Status}}"
        )
        if not line:
            continue
        restarts, oom, started, status = line.split("|")
        health = inspect(name, "{{if .State.Health}}{{.State.Health.Status}}{{end}}")
        path = cgroup_dir(name)
        events = keyed(read_text(path / "memory.events")) if path else {}
        peak = read_text(path / "memory.peak").strip() if path else ""
        state[name] = {
            "restart_count": int(restarts),
            "oom_killed": oom == "true",
            "oom_kill_events": events.get("oom_kill"),
            "started_at": started,
            "status": status,
            "health": health or None,
            "memory_peak": int(peak) if peak.isdigit() else None,
        }
    return state


def http_text(url: str, timeout: float = 10.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode()


def api_metrics() -> str:
    address = inspect(API, f'{{{{(index .NetworkSettings.Networks "{NETWORK}").IPAddress}}}}')
    return http_text(f"http://{address}:{METRICS_PORT}/metrics")


def alloy_snapshot() -> dict[str, Any]:
    pid = inspect(ALLOY, "{{.State.Pid}}")
    if not pid or pid == "0":
        return {"running": False}

    def inside(url: str) -> str:
        return run(
            ["sudo", "nsenter", "-t", pid, "-n", "curl", "-s", "--max-time", "10", url],
            check=False,
        )

    series: dict[str, float] = {}
    for line in inside(ALLOY_METRICS).splitlines():
        name = line.split("{", 1)[0].split(" ", 1)[0]
        if name in ALLOY_SERIES:
            series[name] = series.get(name, 0.0) + float(line.rsplit(" ", 1)[1])
    try:
        components = {
            c["localID"]: c["health"]["state"] for c in json.loads(inside(ALLOY_COMPONENTS))
        }
    except (ValueError, KeyError, TypeError):
        components = {}
    return {"running": True, "series": series, "components": components}


def probe_readiness(url: str, context: ssl.SSLContext) -> dict[str, Any]:
    started = time.perf_counter()
    status = 0
    detail = ""
    try:
        with urllib.request.urlopen(url, timeout=10, context=context) as response:
            status = response.status
            detail = json.loads(response.read()).get("status", "")
    except urllib.error.HTTPError as error:
        status = error.code
        try:
            body = json.loads(error.read())
            detail = json.dumps(
                {k: v.get("status") for k, v in body.get("checks", {}).items()}, sort_keys=True
            )
        except (ValueError, AttributeError):
            detail = "unparsed body"
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        detail = type(error).__name__
    return {
        "t": utc(),
        "epoch": time.time(),
        "status": status,
        "ms": round((time.perf_counter() - started) * 1000.0, 1),
        "detail": detail,
    }


class Stage:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.out: Path = args.out
        self.ready_url = f"https://{args.host}/api/v1/health/ready"
        self.ssl_context = ssl.create_default_context()
        self.stop = threading.Event()
        self.abort_reason: str | None = None
        self.abort_lock = threading.Lock()
        self.k6_started = threading.Event()

    def path(self, suffix: str) -> Path:
        return self.out / f"{self.args.stage}-{suffix}"

    def abort(self, reason: str) -> None:
        with self.abort_lock:
            if self.abort_reason is not None:
                return
            self.abort_reason = reason
        print(f"ABORT {utc()}: {reason}", flush=True)
        run(["docker", "kill", "--signal", "SIGINT", K6_CONTAINER], check=False)

        def hard_stop() -> None:
            time.sleep(20)
            if inspect(K6_CONTAINER, "{{.State.Running}}") == "true":
                run(["docker", "kill", K6_CONTAINER], check=False)

        threading.Thread(target=hard_stop, daemon=True).start()

    def sampler(self) -> None:
        paths = {name: cgroup_dir(name) for name in WATCHED}
        k6_path: Path | None = None
        limit_mib = self.args.api_memory_abort_mib
        floor_mib = self.args.host_available_abort_mib
        with self.path("samples.jsonl").open("w", encoding="utf-8") as sink:
            while not self.stop.is_set():
                if k6_path is None and self.k6_started.is_set():
                    k6_path = cgroup_dir(K6_CONTAINER)
                    if k6_path is not None:
                        paths[K6_CONTAINER] = k6_path
                record: dict[str, Any] = {"t": utc(), "epoch": time.time(), "host": host_sample()}
                record["containers"] = {
                    name: cgroup_sample(path) for name, path in paths.items() if path is not None
                }
                sink.write(json.dumps(record) + "\n")
                sink.flush()
                api_memory = record["containers"].get(API, {}).get("anon")
                if api_memory is not None and api_memory > limit_mib * 1048576:
                    self.abort(
                        f"API anonymous memory {api_memory / 1048576:.0f} MiB > {limit_mib} MiB"
                    )
                available = record["host"]["mem_available"]
                if available is not None and available < floor_mib * 1048576:
                    self.abort(f"host available {available / 1048576:.0f} MiB < {floor_mib} MiB")
                self.stop.wait(1.0)

    def readiness(self) -> None:
        failing_since: float | None = None
        with self.path("readiness.jsonl").open("w", encoding="utf-8") as sink:
            while not self.stop.is_set():
                result = probe_readiness(self.ready_url, self.ssl_context)
                sink.write(json.dumps(result) + "\n")
                sink.flush()
                now = time.monotonic()
                if result["status"] == 200:
                    failing_since = None
                else:
                    failing_since = failing_since or now
                    if now - failing_since >= self.args.readiness_abort_seconds:
                        self.abort(
                            f"readiness failing for {now - failing_since:.0f} s "
                            f"(last: {result['status']} {result['detail']})"
                        )
                self.stop.wait(5.0)

    def wait_for_recovery(self, timeout_s: float = 180.0) -> float | None:
        started = time.monotonic()
        while time.monotonic() - started < timeout_s:
            if probe_readiness(self.ready_url, self.ssl_context)["status"] == 200:
                return round(time.monotonic() - started, 1)
            time.sleep(2.0)
        return None

    def execute(self) -> int:
        args = self.args
        self.out.mkdir(parents=True, exist_ok=True)
        if inspect(API, "{{.State.Running}}") != "true":
            print("refusing: the API container is not running", file=sys.stderr)
            return 2
        first = probe_readiness(self.ready_url, self.ssl_context)
        if first["status"] != 200:
            print(f"refusing: readiness answered {first}", file=sys.stderr)
            return 2

        record: dict[str, Any] = {
            "stage": args.stage,
            "mode": args.mode,
            "vus": args.vus,
            "rate_per_s": args.rate,
            "duration": args.duration,
            "k6_image": K6_IMAGE,
            "target": "http://api:8000 on the pilot's edge network (the production API process)",
            "limits": {
                "api_memory_abort_mib": args.api_memory_abort_mib,
                "host_available_abort_mib": args.host_available_abort_mib,
                "readiness_abort_seconds": args.readiness_abort_seconds,
            },
            "serving": dict(
                line.split("=", 1)
                for line in read_text(Path("/srv/pathable/release.env")).splitlines()
                if "=" in line
            ),
            "before": {
                "at": utc(),
                "containers": container_state(),
                "alloy": alloy_snapshot(),
                "readiness": first,
            },
        }
        self.path("metrics-before.prom").write_text(api_metrics(), encoding="utf-8")

        threads = [
            threading.Thread(target=self.sampler, daemon=True),
            threading.Thread(target=self.readiness, daemon=True),
        ]
        for thread in threads:
            thread.start()
        time.sleep(3.0)  # a few idle samples before the load begins

        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            K6_CONTAINER,
            "--network",
            NETWORK,
            "--memory",
            "512m",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-v",
            f"{HERE}:/load:ro",
            "-v",
            f"{self.out}:/out",
            "-e",
            f"STAGE={args.stage}",
            "-e",
            f"MODE={args.mode}",
            "-e",
            f"VUS={args.vus}",
            "-e",
            f"DURATION={args.duration}",
            *(["-e", f"RATE={args.rate}"] if args.rate is not None else []),
            K6_IMAGE,
            "run",
            "--quiet",
            "--no-color",
            "--out",
            f"csv=/out/{args.stage}-k6-raw.csv.gz",
            "/load/load.js",
        ]
        record["load_started_at"] = utc()
        record["load_started_epoch"] = time.time()
        print(
            f"{record['load_started_at']} start {args.stage}: {args.vus} VUs, "
            f"rate {args.rate or 'closed loop'}, {args.duration}"
        )
        with self.path("k6.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            self.k6_started.set()
            exit_code = process.wait()
        record["load_ended_at"] = utc()
        record["load_ended_epoch"] = time.time()
        record["k6_exit_code"] = exit_code
        time.sleep(3.0)  # the tail of the load in the samples
        self.stop.set()
        for thread in threads:
            thread.join(timeout=15)

        record["readiness_recovery_seconds"] = self.wait_for_recovery()
        record["abort_reason"] = self.abort_reason
        self.path("metrics-after.prom").write_text(api_metrics(), encoding="utf-8")
        record["after"] = {
            "at": utc(),
            "containers": container_state(),
            "alloy": alloy_snapshot(),
        }
        self.path("run.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(
            f"{record['load_ended_at']} end {args.stage}: k6 exit {exit_code}, "
            f"abort {self.abort_reason}, readiness recovered in "
            f"{record['readiness_recovery_seconds']} s"
        )
        print(read_text(self.path("k6.log")).strip().splitlines()[-1:])
        return 0 if self.abort_reason is None else 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stage", required=True, help="Short name, e.g. c10")
    parser.add_argument("--mode", choices=("route", "mix", "search"), default="route")
    parser.add_argument("--vus", type=int, required=True)
    parser.add_argument("--duration", required=True, help="k6 duration, e.g. 60s")
    parser.add_argument(
        "--rate",
        type=float,
        default=None,
        help="Open-loop: requests a second; --vus is then the most in flight at once",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--host", default=os.environ.get("PATHABLE_HOST", "51.222.36.79"))
    parser.add_argument("--api-memory-abort-mib", type=int, default=1300)
    parser.add_argument("--host-available-abort-mib", type=int, default=400)
    parser.add_argument("--readiness-abort-seconds", type=float, default=60.0)
    args = parser.parse_args()
    if not 1 <= args.vus <= 100:
        parser.error("--vus must be between 1 and 100")
    return Stage(args).execute()


if __name__ == "__main__":
    sys.exit(main())
