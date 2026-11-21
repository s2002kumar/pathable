# Observability

How the public pilot reports whether it is up and fast and whether its backups
work, and how it was switched on.

## Current state

Connected. As of 2026-11-15, as the founder reports it, with the machine side
checked the same day ([`vps-load-2026-11-15.json`](../evidence/vps-load-2026-11-15.json)):

| Part                    | State                                                                                                                                                                                                                                      |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Remote write            | Alloy on the host: every batch accepted, 0 failed, 0 retried, 0 pending, through a load test that moved 440–686 samples a stage; all four components healthy                                                                               |
| Ingestion               | `pathable_build_info` visible in Grafana Explore (founder)                                                                                                                                                                                 |
| Dashboard               | `pathable-dashboard.json` imported and live (founder)                                                                                                                                                                                      |
| Availability alert      | **Grafana-managed** rule "PathAble API unavailable", configured in Grafana's UI, state Normal (founder). Its expression lives in Grafana, not in this repository: `availability.rules.yaml` remains the tested reference                   |
| Notification            | An e-mail contact point (founder)                                                                                                                                                                                                          |
| External probe          | Synthetic Monitoring check "PathAble public readiness" on `https://51.222.36.79/api/v1/health/ready`, every 2 minutes, 10 s timeout, from Calgary and Montreal (founder)                                                                   |
| Backup alerts           | **Not yet in Grafana.** The status reaches it from the host; the three rules in `backup.rules.yaml` are imported by the founder ([Backup alerts](#backup-alerts)). Exercised end to end on the real pieces in CI, not yet in Grafana Cloud |
| Route p95 latency alert | **Deliberately not set.** A threshold is recommended, with its reasons, in [VPS_CAPACITY.md](../evidence/VPS_CAPACITY.md#route-p95-alert-threshold-recommendation-not-set)                                                                 |

The write-only Alloy token cannot read series back, so Grafana-side checks are
the founder's. The steps below are how it was set up. Repeat them for a new
stack.

## What runs where

```
API process ──► :9464 (METRICS_PORT, container network only)
                   ▲
                   │ scrape every 30 s
Alloy ─────────────┤
  │                ├── probe https://<host>/api/v1/health/ready every 30 s
  │                │   (through Caddy and TLS, as a visitor reaches it)
  │                └── read /srv/pathable/metrics every 60 s (read-only)
  │                        ▲ written after every backup run (backup_metrics.py)
  ▼
Grafana Cloud (Prometheus) ──► dashboard, alerts
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
- **The backup unit** writes its status after every run (`ExecStopPost`,
  [`backup_metrics.py`](../../infra/vps/backup_metrics.py)) to
  `/srv/pathable/metrics`, which Alloy's textfile collector reads, mounted
  read-only. The directory holds the status and nothing else: Alloy never
  sees a backup, the bucket's name or the AWS key.
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
| `pathable_backup_*`, `node_textfile_scrape_error`        | the backups' status, `job="pathable-backup"` ([below](#backup-status))                     |

A route outcome of **success** means the route for the requested profile was
found. **no_route** means the request was valid but nothing satisfies the
profile — the response may still carry the shortest route. A place search that
looked at nothing (`not_searched`) is never counted as `empty`, for the same
reason the API keeps the two apart.

5xx counts and error rates are derived from `pathable_http_requests_total`
(`status=~"5.."`) rather than kept as a second counter.

### Backup status

Gauges, `job="pathable-backup"`, `instance="waterloo-pilot"` (set in the
Alloy config: the exporter's own `instance` is the container's hostname, which
changes whenever Alloy is recreated):

| Series                                                         | Meaning                                                                                                                                                                                                                                                                 |
| -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pathable_backup_last_run_success{stage}`                      | 1 if the unit's last run ended with its copy verified in S3, else 0. `stage`: `none`, or where a failed run stopped: `dump` (the database side), `upload` (the copy, before S3 held it), `verification` (S3 answered but does not hold these bytes), `timeout`, `other` |
| `pathable_backup_last_run_end_timestamp_seconds`               | when that run ended                                                                                                                                                                                                                                                     |
| `pathable_backup_offsite_last_verified_dump_timestamp_seconds` | when the newest backup S3 is verified to hold was **taken** (0: none)                                                                                                                                                                                                   |
| `pathable_backup_local_last_dump_timestamp_seconds`            | when the newest backup on the host was taken, copied or not                                                                                                                                                                                                             |
| `pathable_backup_offsite_verified_dumps`, `_pending_dumps`     | backups on the host with a receipt; with a manifest and no receipt, which the next run copies                                                                                                                                                                           |
| `pathable_backup_status_refreshed_timestamp_seconds`           | when the status was last worked out                                                                                                                                                                                                                                     |
| `node_textfile_scrape_error`                                   | 1 if Alloy could not read a status file                                                                                                                                                                                                                                 |

**Only a verified copy counts.** A backup counts as off the host only through
its receipt, which `offsite.py` writes once S3, read back, holds exactly the
dump and its manifest, encrypted, and only if the receipt describes the dump
beside it. A copy kept local, deliberately (`PATHABLE_BACKUP_OFFSITE=skip`) or
because it failed, has none. The time that counts is when the backup was taken,
not when it was copied, so an old dump copied late by a retry never passes for
a fresh one.

**No label carries anything else**: no bucket, object or file name, path, AWS
message or database content. The stage is read from that run's own journal
lines and folded into the fixed vocabulary above; without them it is `other`,
never a guess. Alloy forwards only these series: the collector's
`node_textfile_mtime_seconds`, whose label is the file's path, is dropped.

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

`infra/vps/grafana/availability.rules.yaml`, `backup.rules.yaml` and
`latency.rules.yaml.template`, tested by `promtool` in CI
(`alerts.test.yaml`):

- **PathAbleApiUnavailable** (critical) — no readiness probe has succeeded for
  two minutes, or no probe result has arrived for two minutes (host, Docker or
  Alloy down). A deploy's ~60 s readiness gap does not trigger it; the tests
  cover an outage, a deploy-length gap and silence.
- **PathAbleBackupFailed** (warning) — the backup unit's last run failed:
  the dump, its upload to S3, or S3's verification of it. Labelled with the
  stage, and firing until a run succeeds (the next timer run, or
  `systemctl start pathable-backup.service` once the cause is fixed).
- **PathAbleBackupNotVerifiedInS3** (critical) — the newest backup S3 is
  verified to hold was taken over 26 hours ago, or none ever was. The timer
  runs at 07:15 UTC, up to 10 minutes late, so this fires about two hours
  after a day without a verified copy, including a run that never started and
  so reported no failure at all.
- **PathAbleBackupStatusMissing** (warning) — no backup status for 30
  minutes, or a status file Alloy cannot parse. Silence is not health: a host,
  Docker or Alloy that stops, or a status directory that goes missing, would
  otherwise leave the two above with nothing to fire on. A deploy's minute
  without Alloy does not trigger it.
- **PathAbleRouteLatencyP95High** (warning) — p95 of successful
  `POST /api/v1/routes/compare`, server side over ten minutes, above
  `ROUTE_P95_THRESHOLD_SECONDS` for ten minutes.

**The p95 threshold is not set.** It is to be chosen from observed production
latency, not guessed: `render-alerts.sh` refuses to render without it, and CI
checks that it still refuses. The 2026-11-15 load test recommends **5 s**, on a
bucket boundary, together with a minimum-volume guard, because with today's
near-zero traffic one cross-region route is the p95. Both are argued in
[VPS_CAPACITY.md](../evidence/VPS_CAPACITY.md#route-p95-alert-threshold-recommendation-not-set),
and neither is applied pending review. To watch real traffic, read the
dashboard's _Route request latency_ panel, or run in Explore:

```promql
histogram_quantile(0.95, sum by (le) (rate(pathable_http_request_duration_seconds_bucket{route="/api/v1/routes/compare", status_class="2xx"}[1h])))
```

## Backup alerts

**Loading them.** The founder's, since it needs Grafana access; nothing new
is created on the host or in IAM. In Grafana (12.1 or later): _Alerting →
Alert rules_, the data source-managed section, **Import to Grafana-managed
rules**; import source a Prometheus YAML file,
[`backup.rules.yaml`](../../infra/vps/grafana/backup.rules.yaml) as it stands;
the stack's Prometheus data source; any folder. Imported rules notify through
Grafana's own Alertmanager, so the default notification policy sends them to
the e-mail contact point the availability alert already uses
([Grafana's documentation](https://grafana.com/docs/grafana/latest/alerting/alerting-rules/alerting-migration/)).
No token is needed for that. `load-alerts.sh` loads the same file with a
`rules:write` token instead, as data source-managed rules, whose notifications
go to Grafana Cloud's Alertmanager rather than Grafana's; that route has not
been checked for this stack, so prefer the import.

Load them only once the status reaches Grafana Cloud (check
`pathable_backup_last_run_success` in Explore): before that,
PathAbleBackupStatusMissing fires on the missing series, as intended.

**Exercising them.** [VPS.md](VPS.md#knowing-when-a-backup-fails) has the
controlled failure: one run of the real unit made to fail at the upload,
touching neither the database nor the bucket, then a run that succeeds.
`infra/vps/tests/backup-alert-e2e.sh` runs the three alerts on the real
pieces in CI (the status writer, the host's Alloy image and config,
Prometheus, Alertmanager and a webhook), which is evidence for everything but
Grafana Cloud's own evaluation and e-mail.

## If Grafana Cloud refuses the token

Alloy logs `non-recoverable error … 401 Unauthorized … invalid token`, and its
`prometheus_remote_storage_samples_failed_total` equals `samples_total`: every
batch is refused and dropped, while scraping carries on. On 2026-11-09 the token
reached Alloy intact, was a complete `glc_` token for the stack's region, and
was still refused, so the fault was on the Grafana Cloud side of it. Check, in
the portal:

- the access policy's realm includes this stack, and it has `metrics:write`;
- the token is still listed under that policy (not deleted or expired);
- `GRAFANA_CLOUD_PROMETHEUS_USER` is the instance ID on the stack's
  _Prometheus → Details_ page, not the stack or organisation ID.

Then replace the value in `grafana-cloud.env`. Compose reads an env file only
when it creates a container, so Alloy needs recreating, not restarting:
`deploy.sh up <the serving SHA>` does that without restarting the API (the API's
configuration is unchanged; the release's backup is still taken).

## Alloy's footprint, measured

Measured on the pilot on 2026-11-15, after 39 h up and through the load test,
sampled each second ([`vps-load-2026-11-15.json`](../evidence/vps-load-2026-11-15.json)):
**43.5 MiB of anonymous memory, steady**, and about 0.5% of one vCPU. Its
cgroup's `memory.current` sits at the 256 MiB limit, but what fills it is
reclaimable page cache (the WAL's files), not memory Alloy needs. Alloy has not
restarted. `measure.sh` reports `memory.current` alone, which reads as "at the
limit" here, so look at the cgroup's `memory.stat` `anon` before concluding
anything from it.

## What this does not cover

- **Reachability from outside the host** is now Synthetic Monitoring's job (see
  [Current state](#current-state)); Alloy's probe runs on the host it probes and
  cannot see a network path that fails beyond it. Whether the synthetic check
  alerts on its own is configured in Grafana and not recorded here.
- **Whether S3 still holds a backup.** The backup status is the host's record
  of what S3 held when each copy was verified. An object deleted or expired
  later would not show; `offsite.py list` and `fetch` read the bucket itself.
- **Logs and traces.** Logs stay in Docker's rotated JSON files on the host
  (`compose logs`); nothing ships them.
- **Several API workers.** Counters live in the one process (ADR 0009). More
  workers would need prometheus_client's multiprocess mode.
- **Lazy graph loads.** `pathable_graph_load_seconds` is recorded by the startup
  preload, which production uses (`GRAPH_PRELOAD_REGIONS=waterloo`). A process
  that loads its graph on first request instead reports none.
