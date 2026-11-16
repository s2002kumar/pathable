#!/usr/bin/env python3
"""Turn one capacity run's raw outputs into the evidence file.

    python infra/vps/load/summarise.py --host-dir HOST/results --laptop-dir LAPTOP \
        --caddy-log HOST/caddy-ready.jsonl --json docs/evidence/vps-load-YYYY-MM-DD.json

Standard library only. Reads what run_stage.py wrote on the host (k6 summary,
per-second samples, readiness probes, the API's exposition before and after)
and what probe.py wrote on the laptop (baseline, rate limit, watch), and writes
one JSON object. Every number in it is computed from those files; nothing is
typed in.

Three kinds of latency are kept apart, because they answer different questions:

* **client** — k6's round trip from inside the host's edge network: the API's
  own queue plus its work, without the internet or TLS.
* **server** — the API's histograms (``pathable_http_request_duration_seconds``
  and ``pathable_route_comparison_duration_seconds``), differenced across the
  stage and read the way Prometheus's ``histogram_quantile`` reads them: linear
  inside a bucket, so only as fine as the buckets. This is what the dashboard
  and the latency alert see. The public watch's requests are in it too, at
  0.25 a second.
* **public** — probe.py's round trip from the laptop through the internet,
  Caddy and TLS while the stage ran: what a visitor would have waited.

Client addresses never reach the output: the Caddy log's (already truncated)
addresses are dropped, and probes are named by their user agent.
"""

# ruff: noqa: T201 - a command-line tool: it prints where it wrote.

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

API = "pathable-api-1"
CONTAINERS = {
    "api": API,
    "db": "pathable-db-1",
    "caddy": "pathable-caddy-1",
    "web": "pathable-web-1",
    "alloy": "pathable-alloy-1",
    "k6": "pathable-load-k6",
}
MIB = 1048576.0
#: /proc/stat columns: user nice system idle iowait irq softirq steal.
IDLE, IOWAIT, STEAL = 3, 4, 7

_SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+(\S+)$")
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


# ---------------------------------------------------------------------------
# Prometheus exposition and histogram_quantile
# ---------------------------------------------------------------------------


def parse_exposition(text: str) -> list[tuple[str, dict[str, str], float]]:
    samples = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE.match(line)
        if match is None:
            continue
        name, labels, value = match.groups()
        samples.append((name, dict(_LABEL.findall(labels or "")), float(value)))
    return samples


def series_sum(samples: list[tuple[str, dict[str, str], float]], name: str, **wanted: str) -> float:
    return sum(
        value
        for sample_name, labels, value in samples
        if sample_name == name and all(labels.get(k) == v for k, v in wanted.items())
    )


def buckets(
    samples: list[tuple[str, dict[str, str], float]], name: str, **wanted: str
) -> dict[float, float]:
    """Cumulative bucket counts by upper bound, summed over matching series."""
    found: dict[float, float] = {}
    for sample_name, labels, value in samples:
        if sample_name != f"{name}_bucket":
            continue
        if not all(labels.get(k) == v for k, v in wanted.items()):
            continue
        bound = math.inf if labels["le"] == "+Inf" else float(labels["le"])
        found[bound] = found.get(bound, 0.0) + value
    return found


def histogram_quantile(q: float, cumulative: dict[float, float]) -> float | None:
    """Prometheus's ``histogram_quantile``: linear inside the bucket the rank
    falls in, lower bound 0 for the first; a rank in the +Inf bucket answers
    the highest finite bound. ``None`` with no observations."""
    ordered = sorted(cumulative.items())
    if not ordered or ordered[-1][1] <= 0:
        return None
    rank = q * ordered[-1][1]
    lower_bound, lower_count = 0.0, 0.0
    for index, (bound, count) in enumerate(ordered):
        if count >= rank:
            if math.isinf(bound):
                return ordered[index - 1][0] if index > 0 else None
            if count == lower_count:
                return bound
            return lower_bound + (bound - lower_bound) * (rank - lower_count) / (
                count - lower_count
            )
        lower_bound, lower_count = bound, count
    return None


def histogram_delta(
    before: list[tuple[str, dict[str, str], float]],
    after: list[tuple[str, dict[str, str], float]],
    name: str,
    **wanted: str,
) -> dict[str, Any]:
    first = buckets(before, name, **wanted)
    last = buckets(after, name, **wanted)
    delta = {bound: last[bound] - first.get(bound, 0.0) for bound in last}
    count = series_sum(after, f"{name}_count", **wanted) - series_sum(
        before, f"{name}_count", **wanted
    )
    total = series_sum(after, f"{name}_sum", **wanted) - series_sum(before, f"{name}_sum", **wanted)

    def ms(value: float | None) -> float | None:
        return None if value is None else round(value * 1000.0, 1)

    return {
        "count": int(count),
        "mean_ms": ms(total / count) if count else None,
        "p50_ms": ms(histogram_quantile(0.50, delta)),
        "p95_ms": ms(histogram_quantile(0.95, delta)),
        "p99_ms": ms(histogram_quantile(0.99, delta)),
    }


# ---------------------------------------------------------------------------
# Small statistics
# ---------------------------------------------------------------------------


def nearest_rank(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def describe(values: list[float], digits: int = 1) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": round(min(values), digits),
        "p50": round(nearest_rank(values, 0.50), digits),
        "p95": round(nearest_rank(values, 0.95), digits),
        "max": round(max(values), digits),
        "mean": round(statistics.fmean(values), digits),
    }


def k6_trend(summary: dict[str, Any], name: str) -> dict[str, Any] | None:
    metric = summary["metrics"].get(name)
    if metric is None:
        return None
    values = metric["values"]
    keys = {"count": "n", "min": "min", "med": "p50", "avg": "mean", "p(90)": "p90"}
    keys |= {"p(95)": "p95", "p(99)": "p99", "max": "max"}
    return {label: round(values[key], 1) for key, label in keys.items() if key in values}


def k6_count(summary: dict[str, Any], name: str) -> int:
    metric = summary["metrics"].get(name)
    return int(metric["values"].get("count", 0)) if metric else 0


# ---------------------------------------------------------------------------
# One stage
# ---------------------------------------------------------------------------


def resources(rows: list[dict[str, Any]], start: float, end: float) -> dict[str, Any]:
    window = [row for row in rows if start <= row["epoch"] <= end]
    out: dict[str, Any] = {"samples": len(window)}
    for label, name in CONTAINERS.items():
        points = [
            (row["epoch"], row["containers"][name])
            for row in window
            if name in row["containers"]
            and row["containers"][name].get("cpu_usage_usec") is not None
        ]
        if len(points) < 2:
            continue
        rates = [
            (b["cpu_usage_usec"] - a["cpu_usage_usec"]) / 1e4 / (tb - ta)
            for (ta, a), (tb, b) in itertools.pairwise(points)
            if tb > ta
        ]
        span = points[-1][0] - points[0][0]
        used = points[-1][1]["cpu_usage_usec"] - points[0][1]["cpu_usage_usec"]
        anon = [p["anon"] for _, p in points if p.get("anon") is not None]
        current = [p["memory_current"] for _, p in points if p.get("memory_current") is not None]
        out[label] = {
            # Percent of one vCPU; the host has two.
            "cpu_mean_pct_of_one_vcpu": round(used / 1e4 / span, 1),
            "cpu_p95_1s_pct": round(nearest_rank(rates, 0.95), 1),
            "anon_max_mib": round(max(anon) / MIB, 1) if anon else None,
            "memory_current_max_mib": round(max(current) / MIB, 1) if current else None,
        }
    ticks = [row["host"]["cpu_ticks"] for row in window]
    if len(ticks) >= 2:
        delta = [b - a for a, b in zip(ticks[0], ticks[-1], strict=False)]
        total = sum(delta)
        busy = total - delta[IDLE] - delta[IOWAIT]
        out["host"] = {
            "cpu_busy_mean_pct_of_two_vcpu": round(100.0 * busy / total, 1),
            "cpu_steal_pct": round(100.0 * delta[STEAL] / total, 2),
            "cpu_iowait_pct": round(100.0 * delta[IOWAIT] / total, 2),
            "mem_available_min_mib": round(
                min(row["host"]["mem_available"] for row in window) / MIB, 1
            ),
            "swap_used_max_mib": round(max(row["host"]["swap_used"] for row in window) / MIB, 1),
            "cpu_pressure_some_avg10_max": max(
                row["host"]["cpu_pressure_some_avg10"] or 0.0 for row in window
            ),
            "loadavg_1m_max": max(row["host"]["loadavg_1m"] for row in window),
        }
    return out


def readiness(records: list[dict[str, Any]], key: str = "ms") -> dict[str, Any]:
    failed = [r for r in records if r["status"] != 200]
    return {
        "probes": len(records),
        "ok": len(records) - len(failed),
        "failed": len(failed),
        "failed_statuses": dict(Counter(str(r["status"]) for r in failed)),
        "latency_ms": describe([r[key] for r in records]),
    }


def stage_summary(
    host: Path, stage: str, watch: list[dict[str, Any]], caddy: list[dict[str, Any]]
) -> dict[str, Any]:
    run = json.loads((host / f"{stage}-run.json").read_text(encoding="utf-8"))
    summary = json.loads((host / f"{stage}-k6-summary.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (host / f"{stage}-samples.jsonl").open()]
    probes = [json.loads(line) for line in (host / f"{stage}-readiness.jsonl").open()]
    before = parse_exposition((host / f"{stage}-metrics-before.prom").read_text())
    after = parse_exposition((host / f"{stage}-metrics-after.prom").read_text())
    start, end = run["load_started_epoch"], run["load_ended_epoch"]

    duration = k6_trend(summary, "http_req_duration") or {}
    requests = int(summary["metrics"]["http_reqs"]["values"]["count"])
    rate = summary["metrics"]["http_reqs"]["values"]["rate"]
    failed_rate = summary["metrics"]["http_req_failed"]["values"]["rate"]
    route = run["mode"] != "search"
    in_window = [r for r in watch if start <= r["epoch"] <= end]
    public_routes = [r for r in in_window if r["kind"] == "route"]
    public_ready = [r for r in in_window if r["kind"] == "ready"]

    out: dict[str, Any] = {
        "stage": stage,
        "workload": run["mode"],
        "loop": "open" if run.get("rate_per_s") else "closed",
        "vus": run["vus"],
        "arrival_rate_per_s": run.get("rate_per_s"),
        "duration": run["duration"],
        "started_at": run["load_started_at"],
        "ended_at": run["load_ended_at"],
        "aborted": run["abort_reason"],
        "k6_exit_code": run["k6_exit_code"],
        "client": {
            "requests": requests,
            "throughput_per_s": round(rate, 2),
            "error_rate": round(failed_rate, 4),
            "rate_limited": k6_count(summary, "rate_limited"),
            "server_errors": k6_count(summary, "server_errors"),
            "transport_errors_or_timeouts": k6_count(summary, "transport_errors"),
            "latency_ms": duration,
            "time_to_first_byte_ms": k6_trend(summary, "http_req_waiting"),
            "response_bytes": k6_trend(summary, "response_bytes"),
        },
        "correctness": {
            "checked": k6_count(summary, "semantic_matches")
            + k6_count(summary, "semantic_mismatches"),
            "matched": k6_count(summary, "semantic_matches"),
            "mismatched": k6_count(summary, "semantic_mismatches"),
        },
        "resources": resources(rows, start, end),
        "readiness_from_host": readiness(probes),
        "readiness_recovery_seconds": run["readiness_recovery_seconds"],
        "public_during_stage": {
            "routes": len(public_routes),
            "routes_ok": sum(1 for r in public_routes if r["status"] == 200),
            "routes_matching_smoke": sum(1 for r in public_routes if r.get("matches_smoke")),
            "route_round_trip_ms": describe(
                [r["round_trip_ms"] for r in public_routes if r["status"] == 200]
            ),
            "readiness": readiness(public_ready, "round_trip_ms") if public_ready else None,
        },
        "external_monitors": monitors(caddy, start, end + 15.0),
        "containers_after": {
            name.removeprefix("pathable-").removesuffix("-1"): {
                "restart_count": state["restart_count"],
                "oom_killed": state["oom_killed"],
                "health": state["health"],
            }
            for name, state in run["after"]["containers"].items()
        },
        "alloy": alloy(run),
    }
    if route:
        out["correctness"]["smoke_distance_spread_m"] = {
            "standard": k6_trend(summary, "standard_distance_m"),
            "accessible": k6_trend(summary, "accessible_distance_m"),
        }
        out["client"]["server_computation_ms_reported"] = k6_trend(summary, "server_computation_ms")
        compare = {"route": "/api/v1/routes/compare"}
        out["server"] = {
            "http_compare_2xx": histogram_delta(
                before,
                after,
                "pathable_http_request_duration_seconds",
                status_class="2xx",
                **compare,
            ),
            "route_comparison_compute": histogram_delta(
                before, after, "pathable_route_comparison_duration_seconds"
            ),
        }
    else:
        search = {"route": "/api/v1/geocode/search"}
        out["server"] = {
            "http_search_2xx": histogram_delta(
                before,
                after,
                "pathable_http_request_duration_seconds",
                status_class="2xx",
                **search,
            ),
            "place_search": histogram_delta(
                before, after, "pathable_place_search_duration_seconds"
            ),
        }
    if out["loop"] == "closed" and duration.get("mean"):
        # Little's law: in flight = throughput x time in system.
        out["client"]["littles_law_in_flight"] = round(rate * duration["mean"] / 1000.0, 1)
    return out


def alloy(run: dict[str, Any]) -> dict[str, Any]:
    first = run["before"]["alloy"].get("series", {})
    last = run["after"]["alloy"].get("series", {})

    def delta(name: str) -> float:
        return last.get(name, 0.0) - first.get(name, 0.0)

    return {
        "samples_sent": int(delta("prometheus_remote_storage_samples_total")),
        "samples_failed": int(delta("prometheus_remote_storage_samples_failed_total")),
        "samples_retried": int(delta("prometheus_remote_storage_samples_retried_total")),
        "samples_pending_after": last.get("prometheus_remote_storage_samples_pending"),
        "active_series_after": last.get("prometheus_remote_write_wal_storage_active_series"),
        "components_after": sorted(set(run["after"]["alloy"].get("components", {}).values())),
    }


# ---------------------------------------------------------------------------
# External monitors, from Caddy's access log
# ---------------------------------------------------------------------------


def read_caddy(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    probes = []
    for line in path.open(encoding="utf-8"):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        request = entry.get("request", {})
        if request.get("uri") != "/api/v1/health/ready":
            continue
        agent = (request.get("headers", {}).get("User-Agent") or [""])[0]
        if agent.startswith("Blackbox Exporter"):
            who = "alloy_blackbox"
        elif agent.startswith("synthetic-monitoring-agent"):
            who = "grafana_synthetic_monitoring"
        else:
            continue  # this run's own probes, counted elsewhere
        probes.append(
            {
                "epoch": entry["ts"],
                "who": who,
                "status": entry.get("status"),
                "seconds": round(entry.get("duration", 0.0), 2),
            }
        )
    return probes


def monitors(probes: list[dict[str, Any]], start: float, end: float) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for who in ("alloy_blackbox", "grafana_synthetic_monitoring"):
        mine = [p for p in probes if p["who"] == who and start <= p["epoch"] <= end]
        if mine:
            out[who] = {
                "probes": len(mine),
                "failed": sum(1 for p in mine if p["status"] != 200),
                "slowest_s": max(p["seconds"] for p in mine),
            }
    return out


def monitors_overall(probes: list[dict[str, Any]]) -> dict[str, Any]:
    import datetime as dt

    out: dict[str, Any] = {}
    for who in ("alloy_blackbox", "grafana_synthetic_monitoring"):
        mine = [p for p in probes if p["who"] == who]
        if not mine:
            continue
        out[who] = {
            "window": [
                dt.datetime.fromtimestamp(min(p["epoch"] for p in mine), dt.UTC).isoformat(),
                dt.datetime.fromtimestamp(max(p["epoch"] for p in mine), dt.UTC).isoformat(),
            ],
            "probes": len(mine),
            "failed": [
                {
                    "at": dt.datetime.fromtimestamp(p["epoch"], dt.UTC).isoformat(
                        timespec="seconds"
                    ),
                    "status": p["status"],
                    "seconds": p["seconds"],
                }
                for p in mine
                if p["status"] != 200
            ],
            "slower_than_1s": sum(1 for p in mine if p["seconds"] > 1.0),
            "slowest_s": max(p["seconds"] for p in mine),
        }
    return out


# ---------------------------------------------------------------------------
# The whole run
# ---------------------------------------------------------------------------


def idle_watch(watch: list[dict[str, Any]], windows: list[tuple[float, float]]) -> dict[str, Any]:
    """The public watch's requests while no stage ran (with 20 s margins)."""

    def idle(record: dict[str, Any]) -> bool:
        return all(not (a - 20 <= record["epoch"] <= b + 20) for a, b in windows)

    routes = [r for r in watch if r["kind"] == "route" and idle(r)]
    ready = [r for r in watch if r["kind"] == "ready" and idle(r)]
    return {
        "routes": len(routes),
        "routes_matching_smoke": sum(1 for r in routes if r.get("matches_smoke")),
        "route_round_trip_ms": describe([r["round_trip_ms"] for r in routes if r["status"] == 200]),
        "readiness": readiness(ready, "round_trip_ms") if ready else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host-dir", type=Path, required=True)
    parser.add_argument("--laptop-dir", type=Path, required=True)
    parser.add_argument("--caddy-log", type=Path, default=None)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=["dry"],
        help="Stage names to leave out (default: the harness check, 'dry')",
    )
    args = parser.parse_args()

    watch_file = args.laptop_dir / "watch.jsonl"
    watch = [json.loads(line) for line in watch_file.open()] if watch_file.exists() else []
    caddy = read_caddy(args.caddy_log)
    # Every stage the host ran, in the order it ran them.
    runs = sorted(
        (json.loads(path.read_text(encoding="utf-8")) for path in args.host_dir.glob("*-run.json")),
        key=lambda run: run["load_started_epoch"],
    )
    runs = [run for run in runs if run["stage"] not in args.exclude]
    stages = [stage_summary(args.host_dir, run["stage"], watch, caddy) for run in runs]
    windows = [(run["load_started_epoch"], run["load_ended_epoch"]) for run in runs]
    laptop = {
        name: json.loads((args.laptop_dir / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("baseline", "ratelimit", "journey-costs")
        if (args.laptop_dir / f"{name}.json").exists()
    }
    payload = {
        "stages": stages,
        "public_while_idle": idle_watch(watch, windows) if watch else None,
        "public_baseline": laptop.get("baseline"),
        "public_rate_limit": laptop.get("ratelimit"),
        "journey_costs": laptop.get("journey-costs"),
        "external_monitors": monitors_overall(caddy),
    }
    args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
