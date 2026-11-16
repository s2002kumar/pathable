# Pilot capacity under controlled load, 2026-11-15

What the public pilot's production API did under controlled load on one 2-vCPU
host, measured end to end, with every answer checked. This describes this
host, this release and this workload. **It is not a number of users, visitors
or any business scale.** A virtual user (VU) below is a closed loop with no
think time, so 10 VUs means 10 requests in flight at every moment, which is far
heavier than 10 people using the planner.

Data: [`vps-load-2026-11-15.json`](vps-load-2026-11-15.json). Tooling:
[`infra/vps/load/`](../../infra/vps/load/). How to rerun it:
[VPS.md § Measuring capacity](../deployment/VPS.md#measuring-capacity).

|                   |                                                                                                            |
| ----------------- | ---------------------------------------------------------------------------------------------------------- |
| Serving           | `905364e736b18cecee6c80ee72b289b3df900da0` (`0.1.0+905364e736b1`), deployed 2026-11-10; nothing redeployed |
| Host              | OVH VPS, Ubuntu 26.04, 2 vCPU, 3,814 MiB RAM, 2 GiB swap; one API process, cgroup limit 1,536 MiB          |
| Dataset           | `51585450-8ff5-409d-a02e-66d5a3c5e260`, checksum `51e75f78…` in every response; routing policy 3           |
| Window            | 2026-11-15, 23:21–23:49 UTC                                                                                |
| Load generator    | `grafana/k6:1.8.1@sha256:23f22790…` on the host, removed afterwards                                        |
| Answers checked   | 2,925 under load (2,306 routes, 619 searches) plus 250 public routes: **every one correct**                |
| Errors seen by k6 | **0** in every stage; 1 visitor-facing 502 through Caddy (below, fixed in this PR, not deployed)           |

Routes derive from OpenStreetMap data, © OpenStreetMap contributors, ODbL 1.0.

## Method

**Where the load came from.** The API limits each client to 30 route
comparisons and 60 searches a minute, and Caddy discards any `X-Forwarded-For` a
client sends. So load from one machine through the public URL measures only the
limiter. The stages therefore ran from a k6 container on the host's Compose
edge network, straight to the production API process (`api:8000`). Each request
carried the next of 762 synthetic client addresses from the RFC 5737
documentation ranges, so the limiter saw many clients, none near its limit and
none able to collide with a real visitor. No host stage received a 429.
Meanwhile a laptop in Ontario probed through the internet, Caddy and TLS:
readiness every 5 s and the smoke journey every 4 s, inside one client's
budget. The limiter itself was tested separately, through the public path.

**Workloads.** _route_ is the smoke journey (Davis Centre Library to Student
Life Centre, wheelchair profile), with coordinates from production place search
in this run. _mix_ is that journey plus three 1.3–1.6 km journeys from the
evaluation corpus, in turn. _search_ is place search: submitted searches for
both ends of the journey, alternating with as-you-type prefixes. Every 200 was
checked, and any wrong answer would have aborted the stage. Routes had to match
401.3 m / 3 stairways against 503.7 m / 0 on dataset `51585450` with
`ml_predictions_used=false`; mix journeys had to match the answer production
gave sequentially in the same run; searches had to contain the expected place.

**Safety.** Stages ran in order (1, 10, 25, 50 in flight, then mix, open-loop
3/s and 5/s, then search), each followed by at least 60 s idle, and each began
only after the previous one's gates were read. k6 would have been stopped early
if the API's anonymous memory passed 1,300 MiB, host memory available fell
below 400 MiB, or public readiness failed for 60 s without a break (45 s for
the 50-VU stage, to stay well inside the availability alert's two minutes).
None of these triggered. The host was sampled every second: cgroup CPU and
memory per container, host memory, CPU steal and pressure.

**Three latencies, kept apart.** _Client_ is k6's round trip inside the host.
_Server_ is the API's own histograms, differenced across the stage and read the
way `histogram_quantile` reads them, so it is coarse above 2.5 s (the buckets
are 2.5, 5 and 10 s) and capped at 10 s. _Public_ is the laptop's round trip
through the internet while the stage ran.

## Results

### Route comparison, smoke journey

| Stage | Load              | Requests | Throughput | Client p50 |       p95 |       p99 |       max | Server p95 (histogram) |  Public p50 (max) |
| ----- | ----------------- | -------: | ---------: | ---------: | --------: | --------: | --------: | ---------------------: | ----------------: |
| c01   | 1 in flight       |      345 |   5.74 / s |     155 ms |    216 ms |    320 ms | 2,427 ms¹ |                 276 ms |   373 ms (928 ms) |
| c10   | 10 in flight      |      560 |   6.14 / s |   1,577 ms |  1,962 ms |  2,372 ms |  2,429 ms |               2,443 ms |  1,766 ms (2.8 s) |
| c25   | 25 in flight      |      372 |   5.86 / s |   4,114 ms |  5,093 ms |  5,770 ms |  6,384 ms |              8,208 ms² |  4,502 ms (5.4 s) |
| c50   | 50 in flight      |      408 |   6.16 / s |   7,246 ms | 13,062 ms | 20,165 ms | 25,224 ms |           ≥ 10,000 ms² | 5,959 ms (10.6 s) |
| r3    | 3 / s, open loop  |      181 |   3.00 / s |     160 ms |    250 ms |    298 ms |    391 ms |                 243 ms |   305 ms (339 ms) |
| r5    | 5 / s, open loop  |      301 |   5.00 / s |     160 ms |    271 ms |    308 ms |    313 ms |                 243 ms |   357 ms (543 ms) |
| idle  | public watch only |       56 |          — |          — |         — |         — |         — |                      — |   296 ms (400 ms) |

¹ The only request over 400 ms in c01, 8 s in, coinciding with the stage's only
generation-2 garbage collection. That is consistent with a full collection
over the ~1 GB graph heap stalling the loop for about 2 s. Likely, not
verified.
² Interpolation, not slowness the clients saw. At c25, 14% of server times fall
just past the 5 s bucket edge, and linear interpolation across 5–10 s reads
8.2 s where clients measured 5.1 s. Means agree within 3–7% (server 4,457 ms,
client 4,158 ms).

- **Throughput does not grow with concurrency: 5.9–6.2 a second from 1 to 50
  in flight.** Latency grows linearly instead, as Little's law predicts: in
  flight = throughput × mean latency gave 1.0, 9.9, 24.4 and 48.0.
- **The work per request does not change under load.** Comparison compute
  (server histogram) had a median of 130–142 ms at every stage; all the added
  latency is waiting. Mean time not computing (HTTP mean minus compute mean) was
  34 ms at c01 and 20–21 ms at 3/s and 5/s, against 1.5 s at c10, 4.3 s at c25
  and 8.0 s at c50.
- **Open loop, 5 a second (~80% of the ceiling) still answered at p95 271 ms.**
  But k6 spaced those arrivals evenly. Random arrivals at the same rate would
  queue more, and that was not measured.
- **The public path adds about 140 ms** (TLS, Caddy and the network from
  Ontario): 296 ms at p50 while idle, against 155 ms inside the host.

### Mixed journey lengths and place search

| Stage | Load                             | Requests | Throughput | Client p50 |      p95 |      p99 | Bottleneck                                |
| ----- | -------------------------------- | -------: | ---------: | ---------: | -------: | -------: | ----------------------------------------- |
| mix10 | 10 in flight, 0.4–1.6 km in turn |      139 |   2.18 / s |   4,489 ms | 5,204 ms | 5,549 ms | API event loop (102% of one vCPU)         |
| s10   | 10 in flight, place search       |    1,237 |   27.4 / s |     382 ms |   539 ms |   641 ms | PostgreSQL (160% of one vCPU; host 99.8%) |

The mix's mean sequential cost, about 430 ms, predicts 2.3 a second at
saturation; 2.18 was measured. Search is a different bottleneck. With 10 in
flight PostgreSQL took 1.6 vCPU, the whole host's CPU was busy (99.8% of two
vCPU, CPU pressure `some avg10` 95%), and the API used 31% of one vCPU. Search
and routing running together were not measured.

### Unloaded cost by journey

Sequential, through the public path, wheelchair profile. Server time is the sum
of the two routes' `computation_ms`.

| Journey                                    |  Shortest | Server time | Response |
| ------------------------------------------ | --------: | ----------: | -------: |
| Davis Centre Library → Student Life Centre |   401.3 m |      129 ms |    81 KB |
| Bridgeport & Weber → Columbia & King       | 1,196.1 m |      193 ms |    88 KB |
| Laurier → Uptown                           | 1,316.1 m |      357 ms |   130 KB |
| Campus → Laurier                           | 1,464.5 m |      816 ms |   139 KB |
| Grand River Hospital → Kitchener City Hall | 1,564.0 m |      389 ms |   112 KB |
| Belmont → Grand River Hospital             | 1,902.0 m |      785 ms |   132 KB |
| Laurel Creek → Campus                      | 2,370.6 m |      828 ms |   181 KB |
| Erb & Westmount → Uptown                   | 2,439.8 m |      970 ms |   235 KB |
| Uptown → Belmont                           | 2,668.1 m |    1,665 ms |   192 KB |
| The Boardwalk → Uptown                     | 5,491.8 m |    2,341 ms |   259 KB |
| Laurel Creek → Kitchener Market            | 7,765.6 m |    8,597 ms |   522 KB |
| The Boardwalk → Kitchener Market           | 9,107.3 m |   11,176 ms |   491 KB |

Every journey run more than once answered identically each time. The smoke journey's answer
compresses to 7.8 KB with zstd through Caddy. Conestoga Mall → RIM Park
answered `no_route`, because RIM Park's paths do not connect to the rest of the
network ([KI-14](../development/KNOWN_ISSUES.md)).

### Resources

| Stage(s)             | API CPU (mean, of one vCPU) | Host CPU (of two) | API anon memory | Host available (min) | DB CPU | k6 CPU / memory |
| -------------------- | --------------------------: | ----------------: | --------------: | -------------------: | -----: | --------------: |
| c01                  |                         99% |               55% |       1,025 MiB |            1,846 MiB |     3% |     3% / 15 MiB |
| c10, c25, c50, mix10 |                        102% |            56–58% |       1,026 MiB |            1,836 MiB |   3–5% | 2–4% / ≤ 49 MiB |
| r3 / r5              |                   56% / 87% |         33% / 49% |       1,026 MiB |            1,851 MiB |     3% |   3–4% / 46 MiB |
| s10                  |                         31% |             99.8% |       1,026 MiB |            1,857 MiB |   160% |     3% / 17 MiB |

- **Memory does not move with load.** API anonymous memory stayed at
  1,025–1,026 MiB from the first stage to the last, 510 MiB under its limit,
  because the event loop holds one computation at a time. Host memory available
  never fell below 1,836 MiB, swap stayed at 39.6 MiB, and CPU steal was at most
  0.02%. There were no restarts, no OOM events, and Docker's liveness check
  stayed `healthy` throughout.
- **Alloy:** 43.5 MiB anonymous throughout. Its cgroup reads 256 MiB, which is
  its limit, filled with reclaimable page cache. 0.5% of one vCPU.

### Correctness

All **2,306** route answers under load matched. The smoke journey's raw
distances were identical in every one (min = max = 401.3 m and 503.7 m), the
dataset was `51585450` and `ml_predictions_used` was `false` in every one, and
each mix journey reproduced its sequential answer. All **619** checked searches
contained the expected place. Of the laptop's 250 public routes, 249 matched and
one was a 502, below. No semantic drift was seen at any concurrency.

### Rate limit, through the public path

Forty route requests from 10 threads at one address: **32 admitted** (the burst
of 30 plus about 2 refilled during the 5.4 s run), **8 refused** with 429,
`Retry-After` 1–2 s and code `rate_limited`, and no other status. Every admitted
answer was correct.

### Telemetry and monitors

- **Alloy kept shipping.** Every stage moved 440–686 samples with **0 failed,
  0 retried, 0 pending**, and all four components stayed healthy. Active series
  went from 101 to 252 as the route, search and status series came into being.
- **Readiness degraded under saturation.** Through Caddy, readiness took
  1.2–2.4 s at c10 and 4.6–7.2 s at c25. At c50 it answered **503 to three
  consecutive probes** (about 30 s), reporting `database` and `postgis` unavailable with
  "probe exceeded the configured readiness timeout". The database was healthy
  the whole time: every route succeeded and it used 4% CPU. The 2 s dependency
  probe simply could not be scheduled on the busy event loop. mix10 produced one
  more such 503.
- **External monitors, from Caddy's log:** Alloy's probe failed **1 of 70**
  (503 after 4.2 s at 23:37:35, mix10); at c50 it got 200 after 9.8 s, just under
  its 10 s timeout. Grafana Synthetic Monitoring failed **1 of 34** (503 after
  6.2 s at 23:34:40, c50). The availability alert needs every probe in two
  minutes to fail, so neither should have fired it. Confirm in Grafana.

## Defects found

1. **A visitor-facing 502 from a keep-alive race (fixed here, not deployed).**
   At 23:30:19 during c10, Caddy logged
   `read tcp …->api:8000: read: connection reset by peer` and answered a route
   comparison with 502 after 112 ms. uvicorn closes idle keep-alive connections
   after 5 s; Caddy reuses idle upstream connections for two minutes, and does
   not retry a POST. It is the only upstream reset in 40,178 requests in Caddy's
   log since 2026-11-04. The API now keeps idle connections for 180 s, so Caddy
   is always the side that closes one. A regression test ties that value to the
   Caddyfile, and it fails on the old code. Shortening Caddy's timeout instead
   would not be robust: on a saturated loop, uvicorn's timer starts before a
   large answer has left the socket. The fix takes effect only with a deploy.
2. **One request's work is unbounded in practice, and the limit counts requests,
   not work (open, [KI-17](../development/KNOWN_ISSUES.md)).** A 9.1 km journey
   holds the only event loop for 11.2 s. A client within its 30 a minute can
   therefore ask for about 335 s of computation a minute, more than five times
   what the process has. KI-13's "under a tenth of what the process serves" holds only
   for campus-scale journeys.

## Bottleneck and headroom

The **first bottleneck is the API's single event loop**: one process, one vCPU,
reached at about **6 comparisons a second for the campus journey** while the
rest of the host idles (host CPU 57% of two vCPU, database under 5%, memory
flat). The remaining ~0.85 vCPU cannot be used by one process. A second process
needs about another 1 GiB of RAM, which this host does not safely have
([KI-12](../development/KNOWN_ISSUES.md)). Place search has its own bottleneck,
PostgreSQL CPU, which reached the host's limit at 27 searches a second.

## Capacity envelope for this host

Measured behaviour, stated conservatively. Where a figure is a measurement it
says so; the rest is the measured single-loop model applied.

| Condition                                   | Measured                                                                               |
| ------------------------------------------- | -------------------------------------------------------------------------------------- |
| Sequential, one request at a time           | p50 155 ms, p95 216 ms inside the host; 296 ms at p50 through the public path          |
| Ceiling, campus journey                     | 5.9–6.2 comparisons a second, whatever the concurrency (four stages)                   |
| Ceiling, 0.4–1.6 km mix                     | 2.18 a second                                                                          |
| 3–5 a second, evenly spaced, campus journey | p95 250–271 ms, no queueing                                                            |
| 10 in flight                                | p95 2.0 s (campus) / 5.2 s (mix); readiness 1.2–2.4 s, still 200                       |
| 25 in flight                                | p95 5.1 s; readiness 4.6–7.2 s, still 200                                              |
| 50 in flight for 60 s                       | p95 13.1 s, max 25.2 s, **0 errors**; readiness 503 for ~30 s; 1 external probe failed |

**Recommended operating envelope: up to 3 comparisons a second of campus-scale
journeys sustained (about half the measured ceiling), and fewer than 10 in
flight.** Half, because real arrivals are burstier than k6's evenly spaced
ones, and because longer journeys cost more. In general, capacity on this host
is 1 ÷ (mean server time per request). That is about 6 a second at 130 ms,
about 2 a second at 430 ms, and one every 11 s for a cross-region route.
**50 in flight was survivable but is not service**: no errors, no resource
risk, but answers took 7–25 s and readiness turned red. That is the highest
level measured, and it should be treated as the edge rather than a target.

## Route p95 alert threshold: recommendation, not set

**Recommend `ROUTE_P95_THRESHOLD_SECONDS=5`, together with a minimum-volume
condition. Neither is configured; the rule is unchanged pending review.**

- **Use a bucket boundary.** The alert reads `histogram_quantile` over HTTP
  buckets, and above 1.5 s the only boundaries are 2.5, 5 and 10 s. Between
  them it interpolates linearly, which at c25 turned a 5.1 s client p95 into an
  8.2 s server reading. A threshold on a boundary is not moved by that.
- **2.5 s would fire on what people route, not on load.** Unloaded, a 5.5 km
  journey takes 2.34 s of server time, and 1.9–2.7 km journeys take 0.8–1.7 s. A
  traffic mix weighted to journeys like those would put p95 at the 2.5 s edge
  with nobody queueing.
- **5 s fires where waiting became bad.** Server p95 crossed it at c25 (8.2 s;
  clients 5.1 s), mix10 (9.0 s; clients 5.2 s) and c50 (≥ 10 s). It stays clear
  of normal campus-scale traffic (243–276 ms) and of c10 (2.4 s), which is
  degraded but usable.
- **Sample volume makes p95 unreliable today.** The API had served no route
  comparison between the 2026-11-14 reboot and this run. With a handful of
  requests in ten minutes, p95 is simply the slowest one or two, and a single
  cross-region route (8.6–11.2 s) holds the 10-minute p95 at the 10 s cap for
  ten minutes, which is right at the edge of `for: 10m`. Before enabling, add
  a guard so the rule needs at least 60 successful comparisons in the window
  (0.1 a second):
  `and sum(rate(pathable_http_request_duration_seconds_count{route="/api/v1/routes/compare", status_class="2xx"}[10m])) > 0.1`.
- **Consider a saturation signal that ignores journey length.** Mean HTTP time
  minus mean compute time, from the existing histograms' `_sum` and `_count`,
  was 20–34 ms unsaturated, 1.5 s at 10 in flight and 4.2–8.0 s beyond. A
  warning at 1 s of mean waiting would catch saturation regardless of the
  journey mix. Suggested for review only.
- **Even so, this alert will fire if more than 5% of route requests are
  cross-region.** Each such request takes 8.6–11.2 s with no load at all. That
  is a true statement about waiting, not a capacity alarm; KI-17 is the fix.

## Limitations

- One run, one evening, one host. Variance between runs on other days was not
  measured, and neither were long soaks; stages lasted 45–90 s.
- Open-loop arrivals were evenly spaced, not random, and the knee between 5 a
  second and the ceiling was not measured.
- Load entered inside the host, so TLS and Caddy were exercised only by the
  public watch and the monitors, at low rate. Caddy used 0.2% of one vCPU.
- The load generator shared the host: 2–4% of one vCPU and up to 49 MiB.
- Long journeys were measured only sequentially. Running them concurrently would
  have held the loop for minutes, so it was not done.
- Search and routing were not loaded together.
- Laptop and host clocks are NTP-synchronised but not cross-checked;
  stage-window alignment is good to about a second.
- The generation-2 GC explanation of the 2.4 s outlier is an inference from
  counters, not a measurement of the pause.
