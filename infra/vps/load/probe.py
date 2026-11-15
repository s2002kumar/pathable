#!/usr/bin/env python3
"""The public side of a capacity run: what a visitor's requests see, from outside the host.

    python infra/vps/load/probe.py baseline  --url https://51.222.36.79 --out DIR
    python infra/vps/load/probe.py ratelimit --url https://51.222.36.79 --out DIR
    python infra/vps/load/probe.py watch     --url https://51.222.36.79 --out DIR --minutes 25

Standard library only. Every request crosses the public internet, Caddy and
TLS. This script is one client, so it lives inside one client's budget
(30 route comparisons and 60 searches a minute, KI-13) except where it means
to spend it.

* ``baseline`` — liveness, readiness and place search, then the established
  smoke journey routed sequentially, paced under the route budget, each answer
  checked against smoke.py's recorded figures; one compressed transfer; and a
  few longer journeys from the evaluation corpus, to show how per-request cost
  grows with distance. Writes ``journey.json`` beside load.js from what place
  search answered, so the load stages route exactly what a visitor would.
* ``ratelimit`` — 40 route requests from 10 threads at once, from this one
  address. The limit must admit about 30 and refuse the rest with 429,
  ``Retry-After`` and ``rate_limited``; nothing may fail any other way.
* ``watch`` — for the length of the load stages: readiness every 5 s and the
  smoke journey every 4 s (0.25 a second, half the route refill rate), each
  route checked, one JSON line per request. Aligned with the host's samples by
  UTC time afterwards.
"""

# ruff: noqa: S310, T201 - a command-line probe: prints, and opens only the origin given.

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
JOURNEY_FILE = HERE / "journey.json"


def _load_smoke() -> ModuleType:
    """infra/vps/smoke.py, which owns the journey and the figures it must reproduce."""
    spec = importlib.util.spec_from_file_location("pathable_vps_smoke", HERE.parent / "smoke.py")
    if spec is None or spec.loader is None:
        msg = "cannot load infra/vps/smoke.py"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    # Registered first: its dataclasses look their own module up while being built.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke()

#: Longer journeys from the evaluation corpus (routing/waterloo_cases.py), for
#: how server time grows with distance, and for the mixed load stage. Longitude,
#: latitude. (conestoga-to-rim-park is left out: on Waterloo Core v2 its ends
#: lie on parts of the network that do not connect, so it answers no_route.)
LONGER_JOURNEYS = {
    "campus-to-laurier": ((-80.5424, 43.4728), (-80.5275, 43.4740)),
    "laurier-to-uptown": ((-80.5275, 43.4740), (-80.5222, 43.4650)),
    "grand-river-hospital-to-kitchener-city-hall": ((-80.5069, 43.4574), (-80.4925, 43.4516)),
}

#: Seconds between paced route requests: under the 30-a-minute refill.
ROUTE_PACE_S = 2.2


def utc() -> str:
    return dt.datetime.now(tz=dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def fetch(
    url: str, body: dict[str, Any] | None = None, timeout: float = 30.0
) -> tuple[int, dict[str, str], bytes, float]:
    """smoke.request, with a timeout or refused connection recorded as status 0
    rather than raised: under load that is a result, not a crash."""
    started = time.perf_counter()
    try:
        result: tuple[int, dict[str, str], bytes, float] = smoke.request(url, body, timeout)
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, {}, b"", (time.perf_counter() - started) * 1000.0
    return result


def summarise(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)

    def q(fraction: float) -> float:
        return round(ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))], 1)

    return {
        "n": len(values),
        "min": round(ordered[0], 1),
        "p50": q(0.5),
        "p95": q(0.95),
        "max": round(ordered[-1], 1),
        "mean": round(statistics.fmean(values), 1),
    }


def route_body(origin: tuple[float, float], destination: tuple[float, float]) -> dict[str, Any]:
    return {
        "region": "waterloo",
        "origin": {"longitude": origin[0], "latitude": origin[1]},
        "destination": {"longitude": destination[0], "latitude": destination[1]},
        "profile": "wheelchair",
    }


def journey_body(journey: dict[str, Any]) -> dict[str, Any]:
    return {k: journey[k] for k in ("region", "origin", "destination", "profile")}


def read_route(status: int, body: bytes) -> dict[str, Any]:
    """The facts of a route answer this run checks, or why there are none."""
    if status != 200:
        try:
            code = json.loads(body).get("code")
        except ValueError:
            code = None
        return {"status": status, "code": code}
    payload = json.loads(body)
    standard = payload.get("standard_route") or {}
    accessible = payload.get("accessible_route") or {}
    return {
        "status": status,
        "bytes": len(body),
        "standard": [round(standard.get("distance_m", -1), 1), standard.get("stairway_count")],
        "accessible": [
            round(accessible.get("distance_m", -1), 1),
            accessible.get("stairway_count"),
        ],
        "server_ms": round(
            (standard.get("computation_ms") or 0.0) + (accessible.get("computation_ms") or 0.0), 1
        ),
        "dataset_id": (payload.get("dataset") or {}).get("dataset_id"),
        "dataset_checksum": (payload.get("dataset") or {}).get("checksum"),
        "ml_predictions_used": payload.get("ml_predictions_used"),
    }


def matches_smoke(facts: dict[str, Any]) -> bool:
    return (
        facts.get("status") == 200
        and tuple(facts["standard"]) == smoke.EXPECTED_STANDARD
        and tuple(facts["accessible"]) == smoke.EXPECTED_ACCESSIBLE
        and facts["dataset_id"] == smoke.EXPECTED_DATASET
        and facts["ml_predictions_used"] is False
    )


def write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {path}")


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------


def baseline(base: str, out: Path) -> int:
    result: dict[str, Any] = {"mode": "baseline", "url": base, "started_at": utc()}

    for name in ("live", "ready"):
        timings = []
        statuses = []
        for _ in range(10):
            status, _, _, ms = fetch(f"{base}/api/v1/health/{name}")
            timings.append(ms)
            statuses.append(status)
        result[f"health_{name}_round_trip_ms"] = summarise(timings)
        result[f"health_{name}_statuses"] = sorted(set(statuses))

    ends: dict[str, dict[str, Any]] = {}
    search_ms: list[float] = []
    queries = [
        (smoke.JOURNEY_START_QUERY, smoke.JOURNEY_START_LABEL),
        (smoke.JOURNEY_END_QUERY, smoke.JOURNEY_END_LABEL),
        (smoke.CODE_QUERY, smoke.CODE_FIRST_LABEL),
    ]
    for repeat in range(4):
        for query, label in queries:
            status, _, body, ms = fetch(
                f"{base}/api/v1/geocode/search", {"region": "waterloo", "query": query}
            )
            search_ms.append(ms)
            matches = json.loads(body).get("matches", []) if status == 200 else []
            found = next((m for m in matches if m.get("label") == label), None)
            if repeat == 0 and found is not None:
                ends[label] = found
    result["search_round_trip_ms"] = summarise(search_ms)
    if smoke.JOURNEY_START_LABEL not in ends or smoke.JOURNEY_END_LABEL not in ends:
        result["failed"] = "place search did not return both ends of the journey"
        write(out / "baseline.json", result)
        return 1

    start = ends[smoke.JOURNEY_START_LABEL]
    end = ends[smoke.JOURNEY_END_LABEL]
    journey = {
        "region": "waterloo",
        "profile": "wheelchair",
        "origin": {"longitude": start["longitude"], "latitude": start["latitude"]},
        "destination": {"longitude": end["longitude"], "latitude": end["latitude"]},
        "search": {
            "start_query": smoke.JOURNEY_START_QUERY,
            "start_label": smoke.JOURNEY_START_LABEL,
            "end_query": smoke.JOURNEY_END_QUERY,
            "end_label": smoke.JOURNEY_END_LABEL,
        },
        "expected": {
            "dataset_id": smoke.EXPECTED_DATASET,
            "standard": {
                "distance_m": smoke.EXPECTED_STANDARD[0],
                "stairway_count": smoke.EXPECTED_STANDARD[1],
            },
            "accessible": {
                "distance_m": smoke.EXPECTED_ACCESSIBLE[0],
                "stairway_count": smoke.EXPECTED_ACCESSIBLE[1],
            },
        },
    }
    routes: list[dict[str, Any]] = []
    for _ in range(20):
        sent = time.monotonic()
        status, _, body, ms = fetch(f"{base}/api/v1/routes/compare", journey_body(journey))
        facts = read_route(status, body) | {"round_trip_ms": round(ms, 1)}
        facts["matches_smoke"] = facts.get("status") == 200 and matches_smoke(facts)
        routes.append(facts)
        time.sleep(max(0.0, ROUTE_PACE_S - (time.monotonic() - sent)))
    ok = [r for r in routes if r["matches_smoke"]]
    result["route_sequential"] = {
        "requests": len(routes),
        "matching_smoke": len(ok),
        "round_trip_ms": summarise([r["round_trip_ms"] for r in ok]),
        "server_computation_ms": summarise([r["server_ms"] for r in ok]),
        "response_bytes_uncompressed": sorted({r["bytes"] for r in ok}),
        "dataset": sorted({(r["dataset_id"], r["dataset_checksum"]) for r in ok}),
        "statuses": sorted({r["status"] for r in routes}),
    }

    request = urllib.request.Request(
        f"{base}/api/v1/routes/compare",
        data=json.dumps(journey_body(journey)).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept-Encoding": "zstd, gzip",
            "User-Agent": "pathable-load-probe/1",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        compressed = response.read()
        result["route_compressed_transfer"] = {
            "content_encoding": response.headers.get("Content-Encoding"),
            "bytes": len(compressed),
        }
    time.sleep(ROUTE_PACE_S)

    longer: dict[str, Any] = {}
    # The mixed stage routes these too, and checks each answer under load
    # against the one production gave here, sequentially, in the same run.
    mix: list[dict[str, Any]] = []
    for key, (origin, destination) in LONGER_JOURNEYS.items():
        samples = []
        for _ in range(3):
            sent = time.monotonic()
            status, _, body, ms = fetch(
                f"{base}/api/v1/routes/compare", route_body(origin, destination)
            )
            samples.append(read_route(status, body) | {"round_trip_ms": round(ms, 1)})
            time.sleep(max(0.0, ROUTE_PACE_S - (time.monotonic() - sent)))
        good = [s for s in samples if s.get("status") == 200]
        answers = {(tuple(s["standard"]), tuple(s["accessible"])) for s in good}
        longer[key] = {
            "statuses": [s.get("status") for s in samples],
            "standard": sorted({tuple(s["standard"]) for s in good}),
            "accessible": sorted({tuple(s["accessible"]) for s in good}),
            "server_computation_ms": summarise([s["server_ms"] for s in good]),
            "response_bytes_uncompressed": sorted({s["bytes"] for s in good}),
            "round_trip_ms": summarise([s["round_trip_ms"] for s in good]),
        }
        if len(good) == len(samples) and len(answers) == 1:
            ((standard, accessible),) = answers
            body = route_body(origin, destination)
            mix.append(
                {
                    "key": key,
                    "origin": body["origin"],
                    "destination": body["destination"],
                    "standard": {"distance_m": standard[0], "stairway_count": standard[1]},
                    "accessible": {"distance_m": accessible[0], "stairway_count": accessible[1]},
                }
            )
    journey["mix"] = mix
    write(JOURNEY_FILE, journey)
    result["journey"] = journey
    result["longer_journeys"] = longer
    result["finished_at"] = utc()
    write(out / "baseline.json", result)
    return 0 if len(ok) == len(routes) and len(mix) == len(LONGER_JOURNEYS) else 1


# ---------------------------------------------------------------------------
# ratelimit
# ---------------------------------------------------------------------------


def ratelimit(base: str, out: Path, requests: int = 40, clients: int = 10) -> int:
    journey = json.loads(JOURNEY_FILE.read_text(encoding="utf-8"))
    body = journey_body(journey)

    def one(_: int) -> dict[str, Any]:
        status, headers, payload, ms = fetch(f"{base}/api/v1/routes/compare", body)
        facts = read_route(status, payload)
        facts["round_trip_ms"] = round(ms, 1)
        facts["retry_after"] = headers.get("retry-after")
        facts["finished"] = time.monotonic()
        if status == 200:
            facts["matches_smoke"] = matches_smoke(facts)
        return facts

    started_at = utc()
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=clients) as pool:
        results = list(pool.map(one, range(requests)))
    wall = time.monotonic() - started
    results.sort(key=lambda r: r["finished"])
    admitted = [r for r in results if r["status"] == 200]
    refused = [r for r in results if r["status"] == 429]
    other = [r for r in results if r["status"] not in (200, 429)]
    retry_after = sorted({r["retry_after"] for r in refused})
    passed = (
        not other
        and 30 <= len(admitted) <= 32
        and all(r.get("matches_smoke") for r in admitted)
        and all(r.get("code") == "rate_limited" for r in refused)
        and all(value and value.isdigit() and int(value) >= 1 for value in retry_after)
    )
    result = {
        "mode": "ratelimit",
        "url": base,
        "started_at": started_at,
        "clients": clients,
        "requests": requests,
        "wall_s": round(wall, 2),
        "admitted": len(admitted),
        "refused_429": len(refused),
        "other_statuses": sorted({r["status"] for r in other}),
        "retry_after_values": retry_after,
        "refusal_codes": sorted({str(r.get("code")) for r in refused}),
        "admitted_matching_smoke": sum(1 for r in admitted if r.get("matches_smoke")),
        "admitted_round_trip_ms": summarise([r["round_trip_ms"] for r in admitted]),
        "refused_round_trip_ms": summarise([r["round_trip_ms"] for r in refused]),
        "status_sequence_by_completion": [r["status"] for r in results],
        "passed": passed,
    }
    write(out / "ratelimit.json", result)
    return 0 if passed else 1


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------


def watch(base: str, out: Path, minutes: float) -> int:
    journey = json.loads(JOURNEY_FILE.read_text(encoding="utf-8"))
    body = journey_body(journey)
    deadline = time.monotonic() + minutes * 60.0
    lock = threading.Lock()
    sink = (out / "watch.jsonl").open("a", encoding="utf-8", newline="\n")

    def emit(record: dict[str, Any]) -> None:
        with lock:
            sink.write(json.dumps(record) + "\n")
            sink.flush()

    def loop(kind: str, every: float) -> None:
        while time.monotonic() < deadline:
            sent = time.monotonic()
            at, epoch = utc(), time.time()
            if kind == "ready":
                status, _, payload, ms = fetch(f"{base}/api/v1/health/ready", timeout=15)
                record: dict[str, Any] = {"kind": "ready", "status": status}
                if status != 200 and payload:
                    try:
                        checks = json.loads(payload).get("checks", {})
                        record["checks"] = {k: v.get("status") for k, v in checks.items()}
                    except ValueError:
                        pass
            else:
                status, _, payload, ms = fetch(f"{base}/api/v1/routes/compare", body, timeout=60)
                record = {"kind": "route"} | read_route(status, payload)
                if status == 200:
                    record["matches_smoke"] = matches_smoke(record)
                    record.pop("dataset_checksum", None)
            record |= {"t": at, "epoch": epoch, "round_trip_ms": round(ms, 1)}
            emit(record)
            time.sleep(max(0.0, every - (time.monotonic() - sent)))

    threads = [
        threading.Thread(target=loop, args=("ready", 5.0), daemon=True),
        threading.Thread(target=loop, args=("route", 4.0), daemon=True),
    ]
    print(f"{utc()} watching {base} for {minutes} min -> {out / 'watch.jsonl'}", flush=True)
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    sink.close()
    print(f"{utc()} watch finished", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", choices=("baseline", "ratelimit", "watch"))
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--minutes", type=float, default=25.0)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    base = args.url.rstrip("/")
    if args.mode == "baseline":
        return baseline(base, args.out)
    if args.mode == "ratelimit":
        return ratelimit(base, args.out)
    return watch(base, args.out, args.minutes)


if __name__ == "__main__":
    sys.exit(main())
