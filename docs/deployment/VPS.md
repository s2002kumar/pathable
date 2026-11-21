# The public pilot on one VPS

PathAble's Waterloo pilot is served at **<https://51.222.36.79>** from one
OVH VPS. This is the runbook: what runs there, how it was built from nothing,
and how to deploy, roll back, back up, restore and read the logs. The decision
is recorded in [ADR 0009](../adr/0009-deployment-architecture.md); the
measurements are in
[`docs/evidence/vps-deployment.json`](../evidence/vps-deployment.json).

It is a pilot on a single host. Nothing here is highly available, and the
limits are in [KI-12 and KI-13](../development/KNOWN_ISSUES.md).

## What runs

| Host            | OVH VPS, Ubuntu 26.04 LTS, 2 vCPU, 3.7 GiB RAM, 38 GB disk, 2 GiB swap    |
| --------------- | ------------------------------------------------------------------------- |
| Public          | TCP 80 and 443, UDP 443 (Caddy); TCP 22 (SSH, keys only)                  |
| Not public      | PostgreSQL, the API and the web server have no host port at all           |
| Compose project | `pathable`, from [`infra/vps/compose.yaml`](../../infra/vps/compose.yaml) |

```
internet ─▶ caddy :80/:443 ──┬─ /api/v1/* ─▶ api :8000 (one worker, Waterloo graph in memory)
            Let's Encrypt    └─ everything else ─▶ web :3000 (Next.js standalone)
                                          api ─▶ db :5432 (PostGIS, network with no route out)
```

| Service | Image                     | Memory limit | Notes                                                                                |
| ------- | ------------------------- | -----------: | ------------------------------------------------------------------------------------ |
| `db`    | `postgis/postgis:17-3.5`  |        1 GiB | Network `data` only, which is `internal`                                             |
| `api`   | `pathable-api:<full SHA>` |      1.5 GiB | Connects as `pathable_app`: not a superuser, owns only its database                  |
| `web`   | `pathable-web:<full SHA>` |      512 MiB | Built with `NEXT_PUBLIC_API_BASE_URL` = the public origin                            |
| `caddy` | `caddy:2.11.7-alpine`     |      256 MiB | The only container with published ports                                              |
| `alloy` | `grafana/alloy:v1.21.2`   |      256 MiB | Metrics agent; runs only once its credentials exist ([below](#metrics-and-alerting)) |

API, web and Caddy run with `no-new-privileges` and drop every capability
(Caddy keeps `NET_BIND_SERVICE`). The API and web processes run as uid 10001.

**Which revision is live.** `curl https://51.222.36.79/api/v1/health/live`
returns `"version": "0.1.0+<first 12 of the SHA>"`. On the host,
`deploy.sh status` prints the full SHA, the previous one and the database
revision; the images are tagged with the full SHA and carry it in their
`org.opencontainers.image.revision` label.

## Layout on the host

```
/srv/pathable/repo                  clone of the public repository; tooling runs from here
/srv/pathable/stack                 compose.yaml, Caddyfile, db-init/ in use (copied by deploy.sh)
/srv/pathable/secrets/pathable.env  0600: POSTGRES_SUPERUSER_PASSWORD, PATHABLE_DB_PASSWORD
/srv/pathable/secrets/grafana-cloud.env  0600, optional: metrics credentials (OBSERVABILITY.md)
/srv/pathable/secrets/aws-backup.credentials  0600: the backup IAM user's key (below)
/srv/pathable/offsite.env           the S3 bucket, region and prefix for backups (not secret)
/srv/pathable/site.env              PATHABLE_HOST, PATHABLE_TLS_PROFILE (not secret)
/srv/pathable/release.env           the revision being served; release.env.previous before it
/srv/pathable/deployments.log       one line per switch: live and ready times, and backup=
                                    verified-in-s3, local-only (skipped) or none (a rollback)
/var/backups/pathable/              pg_dump archives and their .sha256 files
```

**Secrets.** The two database passwords were generated on the host with
`openssl rand -hex 32`, live only in `secrets/pathable.env` (mode 0600, owner
`ubuntu`), and have never been printed, committed or copied off the host. No
image contains a credential. The superuser is used only over the container's
local socket, by `backup.sh` and `restore.sh`; nothing logs in as it over the
network. The AWS key for backups was typed in by the founder at the host's
terminal (`offsite.py install-credentials`, input hidden) and is read only by
the AWS CLI container, as a read-only mount.

## From nothing to serving

This is the sequence that built the pilot on 2026-10-14/15.

1. **Host.** `sudo sh infra/vps/provision-host.sh` — Ubuntu's own Docker,
   Compose and buildx packages, 2 GiB swap at `vm.swappiness=10`, key-only SSH
   for `ubuntu` with no root login, ufw allowing 22, 80, 443 (TCP) and 443
   (UDP), fail2ban on SSH, rotated Docker logs (`10m` × 5 per container),
   `live-restore`, and unattended security upgrades with a reboot at 08:30 UTC
   when one is required. Idempotent. Reboot after the first run.
2. **Repository and configuration.**

   ```bash
   git clone https://github.com/s2002kumar/pathable.git /srv/pathable/repo
   umask 077
   printf 'POSTGRES_SUPERUSER_PASSWORD=%s\nPATHABLE_DB_PASSWORD=%s\n' \
     "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" > /srv/pathable/secrets/pathable.env
   printf 'PATHABLE_HOST=51.222.36.79\nPATHABLE_TLS_PROFILE=shortlived\n' > /srv/pathable/site.env
   ```

3. **Database.** `infra/vps/deploy.sh init origin/main` builds both images,
   starts PostGIS — whose first start runs
   [`db-init/20-pathable.sh`](../../infra/vps/db-init/20-pathable.sh) to create
   the role, the database and the two extensions — and migrates it to head as
   the application role. It serves nothing.
4. **Dataset.** A data-only archive of the five map tables and the two
   place-index tables, exported from a database holding the verified dataset,
   copied to the host and restored as the application role:

   ```bash
   sh infra/production-smoke/restore-dataset.sh --dump waterloo-main-data.dump \
     --container pathable-db-1 --user pathable_app --db pathable
   ```

   Then the content checksum is **recomputed from the restored rows** and
   compared with the one in
   [`waterloo-dataset-lifecycle.json`](../evidence/waterloo-dataset-lifecycle.json)
   before it is recorded — never recorded first:

   ```bash
   pathable datasets checksum 51585450            # must print 4a179dc0…bc5238f00
   pathable datasets checksum --record 51585450
   ```

   The place index is restored rather than rebuilt: its rows carry the SHA-256
   and data date of the extract they were read from, the same extract as the
   network. Rebuilding it needs the 970 MB extract on the host. An archive
   holding an older gazetteer version than the release reads is upgraded on the
   first `up`; see [Upgrading the place index](#upgrading-the-place-index).

5. **Off-host backups.** Write `/srv/pathable/offsite.env` (bucket, region,
   prefix) and have the founder install the backup key
   ([below](#the-off-host-copy-in-s3)); `offsite.py check` must pass. `up`
   refuses without them, since its backup must reach S3. This step was added
   on 2026-11-18; the pilot was first served without it.
6. **Serve.** `infra/vps/deploy.sh up origin/main`.
7. **Daily backups.** Copy the two units from `infra/vps/systemd/` to
   `/etc/systemd/system/` and `systemctl enable --now pathable-backup.timer`.
   The timer fails its run without a verified S3 copy.

## Deploying

```bash
/srv/pathable/repo/infra/vps/deploy.sh up origin/main      # or a tag, or a SHA on main
```

What it does, in order: refuses a commit that is not on `origin/main` (unless
`ALLOW_UNMERGED=1`); builds `pathable-api:<sha>` and `pathable-web:<sha>` from
`git archive <sha>` — never from a working tree — unless they exist; takes a
`pre-deploy` backup and switches nothing until S3 holds it, verified
([below](#the-off-host-copy-in-s3)), and refuses to continue if the database
is running but cannot be read; restarts the API alone and waits for
`/health/ready`; then brings up web and Caddy; and appends to
`deployments.log`.

The tooling (this directory) runs from whatever `/srv/pathable/repo` has
checked out; the application comes from the SHA you name. Pull the repository
first when the tooling itself changed.

**Expect about a minute without routing.** The API is a single process holding
the graph: the old one takes ~9 s to exit, the new one ~42 s to load the graph
and become ready. Measured from outside, routing was unavailable for about
55 s per switch, and the landing page for a few seconds (two consecutive
2-second samples) when its container was recreated. Otherwise the page stays
up and says the service is unavailable.

To change the public host (for example, when a domain exists): point its DNS
`A` record at `51.222.36.79`, set `PATHABLE_HOST=<domain>` and
`PATHABLE_TLS_PROFILE=classic` in `site.env`, and run `deploy.sh up` — the web
image is rebuilt because the origin is compiled into its bundle.

## Rolling back

```bash
/srv/pathable/repo/infra/vps/deploy.sh rollback
```

Switches to `release.env.previous`, using its images, which stay on the host.
Running it twice toggles between the two releases.

It **refuses** when the database's Alembic revision is not the previous
release's head: an older API cannot start on a schema a newer release
migrated, and would restart forever. In that case restore the `pre-deploy`
backup the forward deploy took, then roll back:

```bash
/srv/pathable/repo/infra/vps/restore.sh --into production --yes \
  --dump /var/backups/pathable/pathable-<time>-pre-deploy-<sha>.dump
/srv/pathable/repo/infra/vps/deploy.sh rollback
```

Exercised on 2026-10-15: `up 465bab7` (the previous merge on `main`) ready in
53.1 s, then `rollback` back to `a3f6448` in 51.6 s; the public version
followed each switch. The refusal path has not been exercised, because no
release since 0007 has changed the schema.

## Upgrading the place index

The place index is data, restored rather than rebuilt here, so a release that
reads a newer gazetteer version than the database holds would migrate the schema
and then serve search from the old index while every other check passed. Waterloo
Core v2 is the first such release: gazetteer version 1 → 2, migration
`0008_place_codes`. Version 1 rows get empty `codes` and `initialisms`, so `DC`,
`SLC` and the rest find nothing by code.

`deploy.sh up` asks first. Before anything switches, it runs the new release's own
`pathable gazetteer status` against the live database, with `--entrypoint
pathable` so the image's migrate-on-start does not run. If the stored index is
not the version the release reads, or was not read from the active network's
extract, the deploy is refused unless it is given an artifact. Readiness reports
the same thing as `place_index` (never as a reason to refuse traffic), and the
smoke test fails on it.

### One-time procedure, version 1 → 2

**1. Build the artifact where the extract is — not on the host.** The host is
too small to read the 970 MB extract beside a running API. On a machine holding
the exact extract the active network came from, against a database holding the
same network at the release's head:

```bash
sha256sum ontario-latest.osm.pbf
# must print cee90e3725139c41ef939f726730b4dfb6fd45e624d2797dac4be38a69145ef3,
# the extract dataset 51585450 records. Never a newer Ontario extract: search
# and routing must describe the same map, and `import` refuses any other.
pathable gazetteer build  --region waterloo --file ontario-latest.osm.pbf
pathable gazetteer status --region waterloo        # must end "current"
pathable gazetteer export --region waterloo --out waterloo-place-index-v2.jsonl.gz
sha256sum waterloo-place-index-v2.jsonl.gz
```

The artifact is the build's provenance and every entry as gzip-compressed JSON
lines, about 0.9 MB. It is OpenStreetMap data under the ODbL, so it is not
committed; it can be rebuilt from the extract at any time. The one prepared for
this upgrade: 941,578 bytes, SHA-256 `8a1dd24bcc249fdbeff359f9009d05bb9b14576ae652de08f317c876380dcabd`,
33,225 entries whose `entries_sha256` is `eb67add8…d7ad`: the same entries as the
index the committed search-quality evidence was measured on.

**2. Copy it to the host** and check it arrived whole:

```bash
scp waterloo-place-index-v2.jsonl.gz ubuntu@51.222.36.79:/srv/pathable/bootstrap/
ssh ubuntu@51.222.36.79 sha256sum /srv/pathable/bootstrap/waterloo-place-index-v2.jsonl.gz
```

**3. Deploy with it:**

```bash
PLACE_INDEX_ARTIFACT=/srv/pathable/bootstrap/waterloo-place-index-v2.jsonl.gz \
  /srv/pathable/repo/infra/vps/deploy.sh up <sha>
```

In order:

1. `gazetteer status` with the new image reports the index is version 1.
2. `gazetteer import --check` with the new image proves the artifact's own content
   (its SHA-256 over every entry, the counts). It also checks that the artifact is
   version 2 and was read from the active network's extract. Nothing is written.
3. The pre-deploy backup is taken. It holds `0007` and the version 1 index.
4. The switch: the API migrates to `0008` and serves. Until the import commits,
   searches answer from the version 1 rows, by name only, and readiness reports
   `place_index: stale`.
5. `gazetteer import` deletes the version 1 index and writes version 2 in one
   transaction, then counts it back before committing.
6. `gazetteer status` must now say `current`. If it does not, the deploy fails.

Then run the smoke test, which checks the place index version and `DC`.

**If the import fails,** nothing was written: the version 1 index still serves,
search is degraded and routing is not. Rerun the same command (the release is
current, so it goes straight to the import), or go back.

### Rolling back after this upgrade

The previous release expects `0007`; after this deploy the database is at `0008`,
so `deploy.sh rollback` refuses on its own. Restore the pre-deploy backup this
deploy took, then roll back:

```bash
/srv/pathable/repo/infra/vps/restore.sh --into production --yes \
  --dump /var/backups/pathable/pathable-<time>-pre-deploy-<sha>.dump
/srv/pathable/repo/infra/vps/deploy.sh rollback
```

The backup brings back the version 1 index with the `0007` schema, which is
exactly what the previous release reads. Rolling forward again needs the
artifact again: `up` refuses without it. Restoring loses anything written to the
database after the deploy; the pilot stores no user data, so that is only what
an operator wrote.

### Rehearsed

On 2026-11-03, against a scratch copy of the production database. The copy used
the production PostGIS image and a role that is not a superuser. `main`'s own
migrations took it to `0007`, it held the same map rows (routing checksum
`4a179dc0…bc5238f00`), and `main`'s code built a version 1 index from the same
extract with production's counts. The release image (`e603872`) ran every step
above in order, with `deploy.sh`'s `docker run` line verbatim. The results:

- The preflight refused (version 1) without migrating anything.
- The check passed.
- The switch migrated to `0008` and served with readiness `ready`,
  `place_index: stale`, and `DC` answering "DC BYTES".
- The import replaced the index in one transaction in 18.2 s. Afterwards `DC`,
  `SLC`, `MC`, `E5` and `PAC` each come first by code.
- The search-quality corpus reproduced the committed results exactly.
- The routing checksum was unchanged.
- The rollback restored `0007` and the version 1 index in 25 s.

`smoke.py` passes its API checks on the upgraded copy. On the stale copy every
check it had before this change passes, and the two new ones fail. Details in
[`place-index-upgrade-rehearsal.json`](../evidence/place-index-upgrade-rehearsal.json).

## Backups and restore

`backup.sh` writes a full custom-format `pg_dump` of the `pathable` database
(32.6 MB, about 5 s) with a `.sha256` and a manifest beside it: daily at
07:15 UTC from the systemd timer, before every deploy, and before every
production restore. The manifest records the file's SHA-256, the release that
was serving, the Alembic revision, the active datasets with their recorded
checksums, the place index and every table's row count. Local files older than
14 days are deleted, never below the newest three. Its output, in
`journalctl -u pathable-backup.service`, is one JSON object per line.

### The off-host copy, in S3

Each backup is then copied by [`offsite.py`](../../infra/vps/offsite.py) to the
S3 bucket named in `/srv/pathable/offsite.env` (`ca-central-1`, prefix
`backups/`), and **a backup is done only once S3 provably holds it**:

- one PUT per file, carrying its SHA-256, which S3 checks on receipt; SSE-S3
  encryption; `If-None-Match`, so an object already there is never replaced.
  The bucket is versioned and expires a replaced version after 7 days, so a
  replacement would quietly destroy the earlier backup a week later;
- then S3's own record of the object — size, SHA-256, encryption — must be
  exactly the local file's. Only then is `<dump>.offsite.json` written;
- a failure keeps the local backup, fails the run (and the timer unit), and
  every later run retries a dump that has a manifest and no receipt;
- nothing on the host deletes from S3. The bucket's lifecycle rule expires
  copies after 30 days, replaced versions after 7, unfinished uploads after 1.

**Which backups require it.** Three do, whether or not `offsite.env` exists:
the timer's (`PATHABLE_BACKUP_OFFSITE=required` in the unit), `deploy.sh up`'s
pre-deploy backup, and the safety backup `restore.sh --into production` takes
before replacing the database. Without `offsite.env`, `up` refuses before it
builds anything. If the copy fails, `up` refuses to switch, and the restore
stops nothing. Run by hand, `backup.sh` keeps its default: it requires the copy
if `offsite.env` exists, and otherwise says its backup is local only.

**Skipping it, on purpose.** During an S3 outage that a deploy cannot wait out,
use `PATHABLE_BACKUP_OFFSITE=skip deploy.sh up <sha>`; the restore takes the
same variable. The backup is then on this host only, and that is written in
three places: a `WARNING` line from the script, an `offsite_skipped` event
from `backup.sh`, and `backup=local-only` in `deployments.log`. Any other value
is refused, `auto` included.

**The key.** A dedicated IAM user may list, upload and download under
`backups/`, including multipart uploads there, and nothing else: on
2026-11-18 deleting, writing or listing outside the prefix, other buckets,
IAM, EC2 and KMS were each refused when tried
([evidence](../evidence/vps-recovery-2026-11-17.json) → `iam`). The founder
installs its key at the host's terminal, as `ubuntu`:

```bash
python3 /srv/pathable/repo/infra/vps/offsite.py install-credentials
```

It reads both values with echo off — and stops rather than read them visibly —
validates their shape, and writes `secrets/aws-backup.credentials` atomically,
mode 0600. Every request runs the AWS CLI image pinned in `offsite.py`, as
`ubuntu`, with that file mounted read-only: the key never appears in a command
line, an environment variable or a log. `offsite.py check` shows the identity
and that the prefix lists, writing nothing; `offsite.py list` lists the copies.

### Proving a backup restores

Non-destructive, any time:

```bash
/srv/pathable/repo/infra/vps/restore.sh --into scratch [--dump FILE] [--route]
```

Restores into a throwaway PostGIS container on its own internal network and
checks the revision, the node, segment and place-index counts, the
sealed-dataset triggers and that the application role is not a superuser.
With a manifest beside the dump, it first checks the dump's SHA-256 against
it, and afterwards every table's row count, the revision and the active
datasets. It then recomputes the dataset's content checksum with the release's
own image. `--route` also starts that image against the restored database and
requires the recorded journey's exact answer; it needs 1,500 MiB available
beside production. Finally it removes the container **and its volume** —
before 2026-11-17 it left the volume, which is how 449.7 MB from 2026-10-15
came to sit unattached on the disk.

**Drilled from S3 on 2026-11-18** ([evidence](../evidence/vps-recovery-2026-11-17.json)
→ `disaster_recovery_drill`). A fresh backup was uploaded and verified, then
downloaded into a separate directory, and its SHA-256 checked again. It was
restored into scratch: 6.3 s of `pg_restore`, every table and the checksum
`4a179dc0…bc5238f00` matched, and the journey answered 401.3 m with 3
stairways against 503.7 m with none. Production answered readiness throughout.

### Replacing the live database

Disaster recovery on this host:

```bash
/srv/pathable/repo/infra/vps/restore.sh --into production --yes [--dump FILE]
```

Takes a `pre-restore` safety backup and waits for S3 to hold it, then stops the
API, drops and recreates the database from the dump, runs the same
verification, and starts the API only if it passed. Exercised on 2026-10-15: 6.5 s of `pg_restore`, checksum matched,
94.9 s from stopping the API to ready again (30 s of that is the checksum).

Images built from `a3f6448` cannot start the `pathable` CLI (pyosmium needs
`libexpat1`, which the base image stopped carrying; fixed in
`services/api/Dockerfile`). For those, `restore.sh` takes
`PATHABLE_CLI_IMAGE=pathable-api-cli:<sha>`, built on the host as the release
image plus that one library:

```bash
printf 'FROM pathable-api:%s\nUSER root\nRUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*\nUSER pathable\n' "$SHA" \
  | docker build -t "pathable-api-cli:$SHA" -
```

### If the host is lost

The S3 copies outlive the host. On a new one: provision it and deploy as in
[From nothing to serving](#from-nothing-to-serving), up to the dataset; put
back `offsite.env` and the key (`install-credentials`); then

```bash
python3 infra/vps/offsite.py fetch latest /srv/pathable/recovery
```

downloads the newest backup and its manifest, checks both against S3 and each
other, and writes the `.sha256` that `restore.sh` checks. Prove it with
`restore.sh --into scratch --dump <file> --route`.

**Not yet exercised: loading it into a new host's live database.**
`restore.sh --into production` expects a running stack (it stops and restarts
the API), and the first-deployment dataset step restores a dataset archive,
not a whole-database backup. The download, the checks and a full restore with
a route answered from it have been done; that last step has not.

## Logs

All four containers log to stdout as JSON where the process supports it, kept
by Docker as `json-file`, rotated at 10 MB with five files per container.

```bash
cd /srv/pathable/stack
docker compose --env-file ../secrets/pathable.env --env-file ../site.env --env-file ../release.env logs -f api
docker logs --since 1h pathable-caddy-1          # access log
journalctl -u pathable-backup.service           # backups and their S3 copies (JSON lines)
```

- **API**: structured JSON, each line tagged with the service, environment and
  version. A route's or search's content is never logged — "Requests are not
  persisted", and the search query is deliberately absent from its log line.
- **Caddy**: one JSON line per request. Client addresses are truncated before
  they are written (IPv4 to /24, IPv6 to /48), and request bodies, where a
  route's origin and destination are, are never logged.
- **PostgreSQL**: statements slower than one second.

## Metrics and alerting

The API exports bounded Prometheus metrics on an unpublished port, and Alloy
ships them, with a probe of public readiness, to Grafana Cloud. **Connected
since 2026-11-15:** the dashboard is live, a Grafana-managed availability alert
e-mails the founder, and Synthetic Monitoring probes readiness from Calgary and
Montreal every two minutes ([OBSERVABILITY.md](OBSERVABILITY.md#current-state)).
The route latency alert is deliberately unset; its recommended threshold is in
[VPS_CAPACITY.md](../evidence/VPS_CAPACITY.md#route-p95-alert-threshold-recommendation-not-set).

## Rate limits

Each client may make **30 route comparisons and 60 searches a minute** (each
figure is both the burst and the refill a minute). Past that the API answers
HTTP 429 with `Retry-After` and code `rate_limited`, and the planner shows that
message. Health, readiness and the profile list are never limited. The limiter
lives in the API process
([`core/rate_limit.py`](../../services/api/src/pathable_api/core/rate_limit.py),
[KI-13](../development/KNOWN_ISSUES.md)), so there is nothing to run beside it.

The client is the `X-Forwarded-For` entry Caddy writes (`TRUSTED_PROXY_HOPS=1`
in [`compose.yaml`](../../infra/vps/compose.yaml)); Caddy discards any a client
sends. That is only safe because the API has no host port — **never publish
port 8000 while `TRUSTED_PROXY_HOPS` is set**, or anyone could choose their own
client address. To change a limit, set `RATE_LIMIT_ROUTES_PER_MINUTE` or
`RATE_LIMIT_SEARCHES_PER_MINUTE` in the API's environment and redeploy; a
refusal appears in the API log as a `Request completed` line with status 429.

## Smoke test and measurements

From any machine with Python 3.11+:

```bash
python infra/vps/smoke.py --url https://51.222.36.79 --expect-revision a3f6448aab55 \
  --runs 10 --concurrency 4 --json smoke.json
```

Nineteen checks:

- the certificate, the HTTP redirect, and that 5432, 8000 and 3000 do not
  answer;
- both pages and their security headers, the reported revision, readiness and
  the profiles;
- that readiness reports the place index as `current`, gazetteer version 2;
- search for both ends of the journey recorded in
  [`waterloo-place-search.json`](../evidence/waterloo-place-search.json), and
  that `DC` finds the William G. Davis Computer Research Centre first, matched
  on its recorded code — which an index older than version 2 cannot do;
- that journey's recorded answer: 401.3 m with 3 stairways, against 503.7 m
  with none for a wheelchair, from dataset `51585450`, with no model
  predictions. Those are routing policy 3's figures as of the Waterloo Core v2
  acceptance pass; a host still running policy 2 answers 4 stairways and
  490.2 m, and fails this check until it is redeployed;
- sequential and parallel route latency, and last, that the rate limit refuses
  this client with a standard 429.

It exits non-zero if anything failed.

The script is one client, so it lives within one client's 30 routes a minute:
the defaults spend 21 before the rate-limit check, and a second run from the
same address within about a minute is refused — wait for the `Retry-After` it
reports. `--skip-rate-limit-check` leaves the budget unspent.

The real browser, against the public site:

```bash
FULLSTACK_TARGET=compose FULLSTACK_WEB_URL=https://51.222.36.79 \
FULLSTACK_API_URL=https://51.222.36.79 \
  pnpm --filter @pathable/web exec playwright test --config=playwright.fullstack.config.ts
```

On the host, `infra/vps/measure.sh 3` force-recreates the API three times and
records start to live and to ready, the graph load, and each container's
cgroup `memory.current` and `memory.peak`. It causes three outages of about a
minute.

## Measuring capacity

[`infra/vps/load/`](../../infra/vps/load/) measures the production API under
controlled load; the last run and what it found are in
[VPS_CAPACITY.md](../evidence/VPS_CAPACITY.md). It restarts and reconfigures
nothing, but a saturating stage makes every visitor wait, so run it when the
pilot is quiet and watch it.

1. **Public baseline, from a laptop** (paced inside one client's budget; it
   also writes `journey.json`, the coordinates and answers the stages check):

   ```bash
   python infra/vps/load/probe.py baseline --url https://51.222.36.79 --out OUT
   ```

2. **Copy `infra/vps/load/` and `infra/vps/smoke.py` to the host**, one file at
   a time (`scp` no longer expands `{a,b}`), into
   `/srv/pathable/evidence/load-<date>/tooling/`, keeping `load/` beside
   `smoke.py`.
3. **Start the public watch** on the laptop for the length of the stages:
   `probe.py watch --url https://51.222.36.79 --out OUT --minutes 30`.
4. **Run each stage on the host** as `ubuntu`, reading its gates before the
   next (`<stage>-run.json`, `-samples.jsonl`, `-readiness.jsonl`), at least
   60 s apart:

   ```bash
   cd /srv/pathable/evidence/load-<date>
   python3 tooling/load/run_stage.py --stage c10 --mode route --vus 10 --duration 90s --out results
   python3 tooling/load/run_stage.py --stage r3 --mode route --vus 50 --rate 3 --duration 60s --out results
   ```

   Modes are `route`, `mix` and `search`; `--rate` makes a stage open-loop.
   k6 runs in the pinned `grafana/k6` image on the `pathable_edge` network, and
   the stage stops itself on API memory, host memory or a minute of failed
   readiness. Remove the image afterwards if disk matters.

5. **Stop the watch, wait a minute, test the limit from outside:**
   `probe.py ratelimit --url https://51.222.36.79 --out OUT`.
6. **Summarise** on the laptop, after copying `results/` back and saving
   Caddy's readiness lines into `caddy-ready.jsonl`:

   ```bash
   docker logs --since <start> --until <end> pathable-caddy-1 2>&1 \
     | grep /api/v1/health/ready > caddy-ready.jsonl        # on the host
   python infra/vps/load/summarise.py --host-dir results --laptop-dir OUT \
     --caddy-log caddy-ready.jsonl --json summary.json      # on the laptop
   ```

Load enters from inside the host because the per-client rate limit and Caddy's
handling of `X-Forwarded-For` make load from one machine through the public URL
measure only the limiter. Each request carries a synthetic client address from
the RFC 5737 documentation ranges, so no real visitor's budget is touched.

## Disk

Images are built on the host, so old releases and their build cache accumulate.
On 2026-11-15 the disk was 78% used. Removing release images older than the
rollback release (`release.env.previous`) and build cache unused for ten days
reclaimed 7,253,262,336 bytes, leaving it 60% used
([`vps-load-2026-11-15.json`](../evidence/vps-load-2026-11-15.json) →
`disk_cleanup`). Keep the serving and rollback images, every volume, the
backups and the evidence. Never run a blind `docker system prune -a --volumes`.
The unattached 449.7 MB volume a scratch restore left on 2026-10-15 was
identified (its cluster's `initdb` time, dockerd's own log of the scratch
container) and removed on 2026-11-18, freeing 444,399,616 bytes
([evidence](../evidence/vps-recovery-2026-11-17.json) → `orphan_volume`);
`restore.sh` no longer leaves one.

## Certificates

Caddy obtains and renews certificates itself. An IP address can only have a
6-day Let's Encrypt certificate (the `shortlived` profile); Caddy renews it
well before expiry. Clients send no SNI to an IP address, so the Caddyfile
sets `default_sni` — without it every verifying client got a TLS
internal-error alert, because inside Docker the connection's local address is
the container's, not the public one.
