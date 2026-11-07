# Observability

How the public pilot reports whether it is up and fast, and how to switch that
on. The repository side is complete. Nothing is connected yet: it needs a
Grafana Cloud account and two tokens, which only the founder can create (see
[Turning it on](#turning-it-on)).

## What runs where

```
API process ──► :9464 (METRICS_PORT, container network only)
                   ▲
                   │ scrape every 30 s
Alloy ─────────────┤
  │                └── probe https://<host>/api/v1/health/ready every 30 s
  │                    (through Caddy and TLS, as a visitor reaches it)
  ▼
Grafana Cloud (Prometheus) ──► dashboard, two alerts
```

- **The API** collects metrics in process (`core/metrics.py`) and serves them on
  a listener of its own, `METRICS_PORT=9464`. No host port publishes it and
  Caddy routes nothing to it; the exposition has no authentication because
  nothing outside the host's container network can reach it. Without
  `METRICS_PORT` metrics are collected and served nowhere.
- **Grafana Alloy** (`grafana/alloy:v1.21.2`, `infra/vps/alloy/config.alloy`)
  scrapes the API, probes public readiness, and remote-writes both. It is the
  only addition to the host, sits in Compose's `observability` profile, and
  starts only when `/srv/pathable/secrets/grafana-cloud.env` exists
  (`deploy.sh` checks). Its own UI stays on the container's loopback, and
  usage reporting to Grafana Labs is off.
- **Grafana Cloud** stores the series and evaluates the alerts.

## Metrics

| Series                                                   | Labels                                                                                     |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `pathable_http_requests_total`                           | `method`, `route` (template), `status` (allowlisted code or class)                         |
| `pathable_http_request_duration_seconds` (histogram)     | `method`, `route`, `status_class`                                                          |
| `pathable_route_comparisons_total`                       | `profile`, `outcome`: success · no_route · invalid · error                                 |
| `pathable_route_comparison_duration_seconds` (histogram) | `profile` — the comparison itself: snapping, both searches, explanation                    |
| `pathable_route_search_duration_seconds` (histogram)     | `kind`: shortest · profile — one per route found                                           |
| `pathable_place_searches_total`                          | `mode`: submit · autocomplete, `outcome`: success · empty · not_searched · invalid · error |
| `pathable_place_search_duration_seconds` (histogram)     | `mode` — the place index's own time                                                        |
| `pathable_graph_load_seconds` (gauge)                    | `region`, `stage`: preload (what readiness waits for) · load (the graph build inside it)   |
| `pathable_build_info` (gauge, always 1)                  | `version`                                                                                  |
| `process_*`, `python_gc_*`, `python_info`                | prometheus_client's process collectors (Linux)                                             |
| `probe_success`, `probe_http_duration_seconds`           | from Alloy's probe, `probe="pathable-public-readiness"`                                    |

A route outcome of **success** means the route for the requested profile was
found. **no_route** means the request was valid but nothing satisfies the
profile — the response may still carry the shortest route. A place search that
looked at nothing (`not_searched`) is never counted as `empty`, for the same
reason the API keeps the two apart.

5xx counts and error rates are derived from `pathable_http_requests_total`
(`status=~"5.."`) rather than kept as a second counter.

### What never becomes a label

Coordinates, search text, client addresses, request ids, segment identities,
dataset ids, exception messages and raw paths. Every label value comes from a
fixed vocabulary in code; anything else folds into `other`, `unmatched` or
`unparsed`. A path scan adds one `unmatched` series, not one per path.
`tests/unit/test_metrics.py` sends each of those things and searches the
exposition for it, and checks every label value against its vocabulary;
`tests/integration/test_routing_api.py` checks computed routes' coordinates and
segment identities stay out, over the synthetic network in PostGIS. Breaking a
collector costs the metric, never the request — both suites test that too.

A local run exercising every endpoint and outcome without a database produced
202 series. Successful routes, process metrics and the probe add to that in
production, but the vocabularies keep it in the hundreds — inside the Free
tier's 10,000.

## Turning it on

Everything below is the founder's to do. Nothing costs money on Grafana Cloud's
**Free tier** (checked 2026-11-06 at grafana.com/pricing: 10k active metric
series, 14-day retention, 3 users, no credit card required).

1. **Create a Grafana Cloud account** and a stack (any region).
2. **Note the Prometheus details** from the stack's _Prometheus → Details_ page:
   the remote-write URL (ends `/api/prom/push`) and the numeric instance ID
   (the username).
3. **Create two access-policy tokens** (_Administration → Cloud access
   policies_):
   - `pathable-alloy`, scope **metrics:write** only. This is the one that lives on
     the pilot host.
   - `pathable-rules`, scopes **rules:read** and **rules:write**. Used once from
     the laptop to load alerts; it does not go on the host.
4. **On the host**, create `/srv/pathable/secrets/grafana-cloud.env`, mode 0600,
   owned like `pathable.env`:

   ```
   GRAFANA_CLOUD_PROMETHEUS_URL=https://prometheus-prod-NN-prod-REGION.grafana.net/api/prom/push
   GRAFANA_CLOUD_PROMETHEUS_USER=<instance ID>
   GRAFANA_CLOUD_PROMETHEUS_TOKEN=<pathable-alloy token>
   ```

   The next `deploy.sh up <sha>` of a release that contains Alloy starts it.
   Never commit the file; it sits beside `pathable.env`, outside the
   repository.

5. **Import the dashboard**: _Dashboards → New → Import_, upload
   `infra/vps/grafana/pathable-dashboard.json`, pick the stack's Prometheus data
   source.
6. **Set up a contact point** (an email address) in _Alerting → Contact points_
   and route the default notification policy to it.
7. **Load the availability alert** once Alloy is running and probe data has
   reached Grafana Cloud (before that, missing data fires it, as a down host
   would):

   ```sh
   MIMIR_ADDRESS=https://prometheus-prod-NN-prod-REGION.grafana.net \
   MIMIR_TENANT_ID=<instance ID> \
   MIMIR_API_KEY=<pathable-rules token> \
     sh infra/vps/grafana/load-alerts.sh
   ```

   `MIMIR_ADDRESS` is the same host as the remote-write URL **without**
   `/api/prom/push`. The script runs `grafana/mimirtool:3.2.2` in Docker.

8. **Add the route latency alert** once the route p95 has been observed for a
   while (see below): the same command with `ROUTE_P95_THRESHOLD_SECONDS=<seconds>`
   in front. `rules load` replaces groups of the same name, so re-running it with
   a new threshold updates the rule in place.

## Alerts

`infra/vps/grafana/availability.rules.yaml` and
`latency.rules.yaml.template`, tested by `promtool` in CI
(`alerts.test.yaml`):

- **PathAbleApiUnavailable** (critical) — no readiness probe has succeeded for
  two minutes, or no probe result has arrived for two minutes (host, Docker or
  Alloy down). A deploy's ~60 s readiness gap does not trigger it; the tests
  cover an outage, a deploy-length gap and silence.
- **PathAbleRouteLatencyP95High** (warning) — p95 of successful
  `POST /api/v1/routes/compare`, server side over ten minutes, above
  `ROUTE_P95_THRESHOLD_SECONDS` for ten minutes.

**The p95 threshold is not set.** It is to be chosen from observed production
latency, not guessed: `render-alerts.sh` refuses to render without it, and CI
checks that it still refuses. After a week of traffic, read the dashboard's
_Route request latency_ panel, or run in Explore:

```promql
histogram_quantile(0.95, sum by (le) (rate(pathable_http_request_duration_seconds_bucket{route="/api/v1/routes/compare", status_class="2xx"}[1h])))
```

## After enabling: measure, do not assume

Alloy's 256 MiB container limit and `GOMEMLIMIT=200MiB` are configuration, not
measurement. After it has run for a day, `sudo /srv/pathable/repo/infra/vps/measure.sh 1`
records its cgroup memory beside the others; add the line to
`docs/evidence/vps-deployment.json`. (`measure.sh` restarts the API once.)

## What this does not cover

- **Reachability from outside the host.** The probe runs on the host it probes,
  so a network path that fails beyond it goes unseen until the host stops
  reporting. Grafana Cloud's Synthetic Monitoring (Free tier: 100k API test
  executions a month) can probe from outside; not configured.
- **Logs and traces.** Logs stay in Docker's rotated JSON files on the host
  (`compose logs`); nothing ships them.
- **Several API workers.** Counters live in the one process (ADR 0009). More
  workers would need prometheus_client's multiprocess mode.
- **Lazy graph loads.** `pathable_graph_load_seconds` is recorded by the startup
  preload, which production uses (`GRAPH_PRELOAD_REGIONS=waterloo`). A process
  that loads its graph on first request instead reports none.
