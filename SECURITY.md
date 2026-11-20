# Security policy

## Project status

PathAble AI is early, advisory software with **one public pilot**: Waterloo,
served at <https://51.222.36.79> from a single VPS since 2026-10-15
([runbook](docs/deployment/VPS.md)). The security posture described here is
what the code and that host establish, not a statement that either has been
audited.

**No formal security audit or penetration test has been performed.**

- **No authentication.** There are no accounts, logins, sessions or cookies.
  Every endpoint is public and anonymous; route requests are rate-limited per
  client address (30 a minute).
- **What a request leaves behind.** A route's origin and destination, and the
  text of a place search, travel in the request body. They are used to answer
  and kept nowhere: the API persists nothing about a request, and its log line
  holds only the method, path, status and duration. Caddy's access log records
  each request's line and headers, its status, and the client address
  truncated to its /24 (IPv4) or /48 (IPv6) network — never the body. Docker
  rotates it at 10 MB, five files, so how long a line survives depends on
  traffic. The rate limiter keeps full client addresses in a bounded table in
  memory only, lost on restart. Metrics sent to Grafana Cloud carry bounded
  labels: no address, coordinate or search text.
- **What the database holds:** the OpenStreetMap-derived network, the place
  index and dataset evidence, and no user data. Its backups, on the host and
  copied to Amazon S3 (`ca-central-1`, SSE-S3, 30 days), hold the same.

## Reporting a vulnerability

Report privately. Do not open a public issue for a security problem.

- Preferred: GitHub → **Security** → **Report a vulnerability** (private
  advisory) on this repository.
- Otherwise: contact the maintainer directly through the address on their GitHub
  profile.

Please include what you found, how to reproduce it, and what you think the
impact is. As a pre-release student-led project there is no bounty programme and
no formal SLA; expect an acknowledgement within about a week.

## Testing against the pilot

The pilot is one small host that people may use to plan a real journey.
**Nobody is authorised to test it destructively**: no load or denial-of-service
testing, fuzzing, brute force, vulnerability scanners or other high-volume
traffic, and no attempt to reach its data, host or accounts. Using the site and
making ordinary requests at a human pace is fine. To test anything heavier, run
the stack yourself ([`README.md`](README.md)), or ask first through a private
advisory.

## Supported versions

Only the `main` branch. Tags mark audited points in its history —
`v0.3.0-public-release` is the first one published with the repository — but
they are not maintained branches, and no fix will be backported to one.

## What the foundation enforces

| Control                        | Where                                                                                                                                  |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| No secrets in the repository   | `.env` git-ignored; gitleaks scans full history in CI                                                                                  |
| No secrets in container images | Nothing but code is copied in; configuration is runtime-only                                                                           |
| No secret-shaped public config | CI rejects `NEXT_PUBLIC_*KEY/TOKEN/SECRET/PASSWORD`                                                                                    |
| Non-root containers            | Both images run as uid 10001                                                                                                           |
| Restricted CORS                | Explicit origin allow-list; wildcard rejected in production                                                                            |
| No credentialed wildcard CORS  | `allow_credentials` is structurally always `false`                                                                                     |
| Sanitised API errors           | One error envelope; no tracebacks, DSNs or driver messages                                                                             |
| No credentials in logs         | Only a `host:port/database` summary is ever logged                                                                                     |
| Dependency auditing            | `pip-audit` and `pnpm audit` weekly and on every PR                                                                                    |
| Pinned CI supply chain         | Every third-party action pinned to a commit SHA                                                                                        |
| Minimal CI permissions         | `contents: read`; no secrets needed for PR CI                                                                                          |
| Least-privilege backup key     | One IAM user: list, upload and download under one S3 prefix; delete, other buckets, IAM, EC2 and KMS all refused when tried            |
| Verified off-host backups      | S3 checks each file's SHA-256 on upload and it is read back; never overwritten; SSE-S3; the key is never on a command line or in a log |

## Threat model

A lightweight threat model, including the privacy and safety risks specific to
an accessibility routing product, is in
[`docs/security/THREAT_MODEL.md`](docs/security/THREAT_MODEL.md).

Two risks there are worth repeating because they are unusual and serious:

1. **Misleading accessibility claims.** A route presented as accessible when it
   is not can strand a wheelchair user at an unramped kerb after dark. This is a
   safety issue, not a UX issue.
2. **Location privacy.** Origin and destination pairs are among the most
   sensitive data a person can hand over. The pilot uses them to answer and
   keeps neither; storing or logging them must not begin without an explicit
   privacy design.

## Disclosure

Fixes land on `main` and are described in the pull request. Once releases exist,
security-relevant fixes will be called out in the release notes.
