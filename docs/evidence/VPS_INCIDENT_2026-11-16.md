# Pilot outage, 2026-11-16: the host was powered off

**For 13 h 40 min nothing at <https://51.222.36.79> answered** — no page, no
route, no SSH — because the OVH subscription for the VPS had lapsed and the
VM was powered off from outside. It came back without intervention once the
founder renewed the subscription. No data was lost, and nothing in the stack
failed.

The data behind this record is in
[`vps-recovery-2026-11-17.json`](vps-recovery-2026-11-17.json) → `incident`;
the raw files are on the host in `/srv/pathable/evidence/recovery-20261117/`.

## Timeline (UTC)

| When                | What                                                                                                      | Seen in               |
| ------------------- | --------------------------------------------------------------------------------------------------------- | --------------------- |
| 2026-11-15          | The OVH subscription expired                                                                              | the founder's account |
| 2026-11-16 06:02:32 | `f3f1a76` deployed (the keep-alive fix)                                                                   | `deployments.log`     |
| 07:22:53            | The day's scheduled backup was written                                                                    | backup unit journal   |
| 09:40:30.707        | `qemu-ga: guest-shutdown called, mode: powerdown`: the hypervisor's guest agent asked the VM to power off | system journal        |
| 09:40:30.714        | `systemd-logind: The system will power off now!`; services stopped in order                               | system journal        |
| 09:40:33            | PostgreSQL shut down cleanly; the journal stopped; power off                                              | PostgreSQL log        |
| —                   | Off: TCP 22 and 443 unreachable                                                                           | the founder           |
| 2026-11-16          | The founder renewed the subscription                                                                      | the founder's account |
| 23:19:56.96         | The kernel booted                                                                                         | system journal        |
| 23:20:06            | Docker started all five containers; none has restarted since                                              | `docker inspect`      |
| 23:20:07.748        | PostgreSQL ready, without crash recovery ("was shut down at 2026-11-16 09:40:33")                         | PostgreSQL log        |
| 23:20:18            | The first public probes reached Caddy, and got 502 while the API loaded its graph                         | Caddy access log      |
| 23:20:56.6          | API ready (graph loaded at 23:20:49.6); readiness answered 200                                            | API log, Caddy log    |
| 2026-11-17 01:10    | Caddy renewed the 6-day certificate on its normal schedule; the old one was valid until 11-20             | Caddy log             |

**Impact.** Routing was unavailable from 09:40:30.7 to 23:20:56.6 — 13 h 40 min
26 s; the host itself was off for 13 h 39 min 24 s. The last backup was 2 h 18
min old when the host went down, and nothing was written in between: route
and search requests are not stored, so the database changes only through
operator commands.

## Cause, and what is not known

The VM's own log says the shutdown came from outside it — a QEMU guest-agent
call, which the hypervisor makes — and that it was orderly. That fits the
provider acting on the lapsed subscription. What the VM cannot show: the
provider's own times for the expiry, the suspension and the renewal. Those
come from the founder.

**Not observed** in any boot's journal: a disk, memory, OOM, kernel, network
or container failure, or a package upgrade around it. The kernel,
7.0.0-43-generic, is the same before and after. The firewall, the SSH
configuration (keys only, `ubuntu` only) and the listening ports are
unchanged.

**Not known: what monitoring did.** The Grafana-managed availability alert and
Synthetic Monitoring should both have recorded more than 13 hours of failures.
Their state was not read — the token on the host is write-only — so whether
the alert fired and e-mailed is the founder's to confirm.

## Checked after it came back (2026-11-17, 03:19–03:25 UTC)

Without restarting or redeploying anything:

- **The release:** serving `f3f1a76c9d28a35c2d15e33b2a7b923aebc254cc`, database
  `0008_place_codes`.
- **The data:** the content checksum of dataset `51585450` recomputed from its
  rows matches `4a179dc0…bc5238f00`, and the `dataset_versions` rows are
  identical to those recorded after the deploy of 2026-11-16.
- **Smoke:** 19/19 from outside and 19/19 from the host. The searched journey is
  401.3 m with 3 stairways against 503.7 m with none, with no model
  predictions.
- **Alloy:** healthy, its remote write at 0 failed, 0 retried and 0 pending
  samples.
- **Exposure:** metrics, PostgreSQL, the API and the web server answer nothing
  from outside.

## What changed because of it

- **Backups now leave the host** (2026-11-18). Each is copied to S3 and counts
  only once S3 provably holds it, and a copy downloaded from S3 was restored
  and routed over ([VPS.md](../deployment/VPS.md#the-off-host-copy-in-s3)).
  Had the VPS been deleted rather than powered off, the backups on its disk
  would have gone with it.
- **Still open:** restoring onto a new host has not been rehearsed. Nothing on
  the host can prevent or announce a lapsed provider account
  ([KI-12](../development/KNOWN_ISSUES.md#ki-12--the-public-pilot-is-one-host)).
  Renewal reminders or auto-renewal are the founder's decision.
