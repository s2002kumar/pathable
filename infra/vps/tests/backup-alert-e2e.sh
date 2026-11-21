#!/bin/sh
# The backup alerts, end to end, on the real pieces: backup_metrics.py writes
# the status, Alloy (the image and config the host runs) reads it and
# remote-writes it, Prometheus evaluates backup.rules.yaml, and Alertmanager
# delivers each notification to a webhook. Run in CI; needs only Docker.
#
#   sh infra/vps/tests/backup-alert-e2e.sh [report.jsonl]
#
# Grafana Cloud is not involved: this proves the status, the agent's handling
# of it and the rules, not Grafana's evaluation or its e-mail. Two intervals
# are shortened so the run takes a minute rather than ten: Alloy's backup
# scrape (60 s to 10 s, its timeout) and the rule group's evaluation (1 m
# to 5 s). Nothing else is changed. No bind mounts, so it runs from any
# Docker client.
#
# In order, each waited for at the webhook:
#   1. no status at all yet         PathAbleBackupStatusMissing fires
#   2. a verified backup an hour old PathAbleBackupStatusMissing resolves
#   3. a run that failed at upload   PathAbleBackupFailed fires, stage=upload
#   4. the next run succeeds         PathAbleBackupFailed resolves
#   5. 27 h since the newest verified backup, no run since
#                                    PathAbleBackupNotVerifiedInS3 fires
# and nothing else: no other alert, and none of these early.

set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
VPS="$(cd "$HERE/.." && pwd)"
REPORT="${1:-}"
ALLOY_IMAGE="grafana/alloy:v1.21.2"
PROMETHEUS_IMAGE="prom/prometheus:v3.15.1"
ALERTMANAGER_IMAGE="prom/alertmanager:v0.28.1"
PYTHON_IMAGE="python:3.13-slim"
WAIT_SECONDS="${WAIT_SECONDS:-120}"
RUN="pathable-alert-e2e-$$"
export MSYS_NO_PATHCONV=1

work="$(mktemp -d)"
cleanup() {
    docker rm -f "$RUN-alloy" "$RUN-prometheus" "$RUN-alertmanager" "$RUN-webhook" "$RUN-writer" \
        > /dev/null 2>&1 || true
    docker network rm "$RUN" > /dev/null 2>&1 || true
    docker volume rm "$RUN-status" > /dev/null 2>&1 || true
    rm -rf "$work"
}
trap cleanup EXIT

log() { printf '%s e2e: %s\n' "$(date -u +%H:%M:%S)" "$1"; }
# A local path as the Docker client wants it: unchanged, except under Git Bash
# on Windows, whose docker.exe needs a Windows path.
local_path() { cygpath -w "$1" 2>/dev/null || printf '%s' "$1"; }
now_ms() { python3 -c 'import time; print(int(time.time() * 1000))' 2>/dev/null || date +%s000; }

# --- the pieces ------------------------------------------------------------------

sed 's/scrape_interval = "60s"/scrape_interval = "10s"/' "$VPS/alloy/config.alloy" > "$work/config.alloy"
grep -q 'scrape_interval = "10s"' "$work/config.alloy"
sed 's/^    interval: 1m$/    interval: 5s/' "$VPS/grafana/backup.rules.yaml" > "$work/backup.rules.yaml"
grep -q '^    interval: 5s$' "$work/backup.rules.yaml"

cat > "$work/prometheus.yml" <<'EOF'
global:
  evaluation_interval: 5s
rule_files: [/etc/prometheus/backup.rules.yaml]
alerting:
  alertmanagers:
    - static_configs:
        - targets: [alertmanager:9093]
EOF

cat > "$work/alertmanager.yml" <<'EOF'
route:
  receiver: webhook
  group_by: [alertname, stage]
  group_wait: 1s
  group_interval: 1s
  repeat_interval: 1h
receivers:
  - name: webhook
    webhook_configs:
      - url: http://webhook:8080/
        send_resolved: true
EOF

# Every notification Alertmanager delivers, one JSON object per line.
cat > "$work/webhook.py" <<'EOF'
import http.server

class Hook(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        with open("/tmp/notifications.jsonl", "ab") as out:
            out.write(body.replace(b"\n", b"") + b"\n")
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass

http.server.HTTPServer(("", 8080), Hook).serve_forever()
EOF

# The status a host would write: backups as backup.sh and offsite.py leave
# them, then backup_metrics.py as the unit runs it, with systemd's variables
# and a journalctl that prints the run's own lines.
cat > "$work/status.py" <<'EOF'
import datetime as dt, json, os, shutil, subprocess, sys
from pathlib import Path
sys.path.insert(0, "/e2e")
import offsite

scenario = sys.argv[1]
backups = Path("/e2e/backups")
shutil.rmtree(backups, ignore_errors=True)
backups.mkdir()

def dump(hours_ago, verified):
    taken = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours_ago)
    name = f"pathable-{taken:%Y%m%dT%H%M%SZ}-scheduled-e2e0e2e0e2e0.dump"
    path = backups / name
    path.write_bytes(b"PGDMP e2e")
    sha = offsite.file_sha256(path)
    (backups / (name + ".manifest.json")).write_text(json.dumps({"format": offsite.MANIFEST_FORMAT, "file": name, "sha256": sha}))
    if verified:
        (backups / (name + ".offsite.json")).write_text(json.dumps({
            "format": offsite.RECEIPT_FORMAT, "file": name,
            "objects": [{"object": "backups/" + name, "sha256": sha},
                        {"object": "backups/" + name + ".manifest.json", "sha256": "b" * 64}]}))

journal = []
if scenario == "verified-an-hour-ago":
    dump(1, verified=True); result = "success"
elif scenario == "failed-at-upload":
    dump(25, verified=True); dump(0, verified=False); result = "exit-code"
    journal = [{"component": "offsite", "event": "offsite_failed", "reason": "upload failed"},
               {"component": "backup", "event": "backup_failed", "label": "scheduled", "offsite": "failed"}]
elif scenario == "succeeded":
    dump(0, verified=True); result = "success"
elif scenario == "27h-since-verified":
    dump(27, verified=True); result = None
else:
    raise SystemExit(f"unknown scenario {scenario}")

Path("/e2e/journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in journal))
env = {**os.environ, "PATHABLE_METRICS_DIR": "/status", "PATHABLE_BACKUP_DIR": str(backups)}
if result is None:
    # The timer never ran: no run is recorded, only what the backups say now.
    command = "refresh"
else:
    command = "record-run"
    env.update(SERVICE_RESULT=result, INVOCATION_ID="e2e0" * 8, EXIT_STATUS="0" if result == "success" else "1")
subprocess.run([sys.executable, "/e2e/backup_metrics.py", command], env=env, check=True)
EOF

docker network create "$RUN" > /dev/null
docker volume create "$RUN-status" > /dev/null

docker create --name "$RUN-webhook" --network "$RUN" --network-alias webhook "$PYTHON_IMAGE" \
    python3 /webhook.py > /dev/null
docker cp "$(local_path "$work/webhook.py")" "$RUN-webhook:/webhook.py"
docker start "$RUN-webhook" > /dev/null

docker create --name "$RUN-writer" --network "$RUN" -v "$RUN-status:/status" "$PYTHON_IMAGE" \
    sleep infinity > /dev/null
docker cp "$(local_path "$VPS/offsite.py")" "$RUN-writer:/tmp/offsite.py"
docker cp "$(local_path "$VPS/backup_metrics.py")" "$RUN-writer:/tmp/backup_metrics.py"
docker cp "$(local_path "$work/status.py")" "$RUN-writer:/tmp/status.py"
docker start "$RUN-writer" > /dev/null
docker exec "$RUN-writer" sh -c '
    mkdir -p /e2e && mv /tmp/offsite.py /tmp/backup_metrics.py /tmp/status.py /e2e/
    printf "#!/bin/sh\ncat /e2e/journal.jsonl\n" > /usr/local/bin/journalctl
    chmod 755 /usr/local/bin/journalctl'

docker create --name "$RUN-alertmanager" --network "$RUN" --network-alias alertmanager \
    "$ALERTMANAGER_IMAGE" --config.file=/etc/alertmanager/e2e.yml > /dev/null
docker cp "$(local_path "$work/alertmanager.yml")" "$RUN-alertmanager:/etc/alertmanager/e2e.yml"
docker start "$RUN-alertmanager" > /dev/null

docker create --name "$RUN-prometheus" --network "$RUN" --network-alias prometheus \
    "$PROMETHEUS_IMAGE" --config.file=/etc/prometheus/e2e.yml --web.enable-remote-write-receiver \
    > /dev/null
docker cp "$(local_path "$work/prometheus.yml")" "$RUN-prometheus:/etc/prometheus/e2e.yml"
docker cp "$(local_path "$work/backup.rules.yaml")" "$RUN-prometheus:/etc/prometheus/backup.rules.yaml"
docker start "$RUN-prometheus" > /dev/null

# As compose runs it: its own user, no capabilities, the status read-only.
docker create --name "$RUN-alloy" --network "$RUN" --user 473:473 --cap-drop ALL \
    -v "$RUN-status:/var/lib/pathable-metrics:ro" \
    -e PATHABLE_HOST=pilot.example.invalid \
    -e GRAFANA_CLOUD_PROMETHEUS_URL=http://prometheus:9090/api/v1/write \
    -e GRAFANA_CLOUD_PROMETHEUS_USER=e2e -e GRAFANA_CLOUD_PROMETHEUS_TOKEN=e2e \
    "$ALLOY_IMAGE" run --server.http.listen-addr=127.0.0.1:12345 \
    --storage.path=/var/lib/alloy/data --disable-reporting /etc/alloy/e2e.alloy > /dev/null
docker cp "$(local_path "$work/config.alloy")" "$RUN-alloy:/etc/alloy/e2e.alloy"
docker start "$RUN-alloy" > /dev/null

# --- the steps -------------------------------------------------------------------

# delivered ALERTNAME STATUS [STAGE]: has the webhook received that notification?
delivered() {
    docker exec -i "$RUN-webhook" python3 - "$@" <<'EOF'
import json, sys
name, status = sys.argv[1], sys.argv[2]
stage = sys.argv[3] if len(sys.argv) > 3 else None
try:
    lines = open("/tmp/notifications.jsonl").read().splitlines()
except FileNotFoundError:
    sys.exit(1)
for line in lines:
    for alert in json.loads(line)["alerts"]:
        labels = alert["labels"]
        if labels["alertname"] == name and alert["status"] == status and labels.get("stage") == stage:
            sys.exit(0)
sys.exit(1)
EOF
}

step=0
expect() {
    step=$((step + 1))
    started="$(now_ms)"
    deadline=$(($(date +%s) + WAIT_SECONDS))
    until delivered "$@"; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
            log "step $step: no '$2' notification for $1 within ${WAIT_SECONDS}s"
            docker logs "$RUN-alloy" 2>&1 | tail -n 20
            docker exec "$RUN-webhook" cat /tmp/notifications.jsonl 2>/dev/null || true
            exit 1
        fi
        sleep 1
    done
    seconds="$(awk -v a="$started" -v b="$(now_ms)" 'BEGIN { printf "%.1f", (b - a) / 1000 }')"
    log "step $step: $1 $2${3:+ (stage $3)} delivered after ${seconds}s"
    if [ -n "$REPORT" ]; then
        printf '{"step":%d,"alert":"%s","status":"%s","stage":"%s","delivered_after_s":%s}\n' \
            "$step" "$1" "$2" "${3:-}" "$seconds" >> "$REPORT"
    fi
}

status() { docker exec "$RUN-writer" python3 /e2e/status.py "$1" > /dev/null; }

log "no status written yet"
expect PathAbleBackupStatusMissing firing

status verified-an-hour-ago
expect PathAbleBackupStatusMissing resolved

status failed-at-upload
expect PathAbleBackupFailed firing upload

status succeeded
expect PathAbleBackupFailed resolved upload

status 27h-since-verified
expect PathAbleBackupNotVerifiedInS3 firing

# Exactly those, in that order. In step 3 the newest verified backup is 25 h
# old, so the 26-hour alert must not have fired yet.
docker exec -i "$RUN-webhook" python3 - <<'EOF'
import json, sys
seen = []
for line in open("/tmp/notifications.jsonl").read().splitlines():
    for alert in json.loads(line)["alerts"]:
        key = (alert["labels"]["alertname"], alert["status"], alert["labels"].get("stage"))
        if key not in seen:
            seen.append(key)
expected = [
    ("PathAbleBackupStatusMissing", "firing", None),
    ("PathAbleBackupStatusMissing", "resolved", None),
    ("PathAbleBackupFailed", "firing", "upload"),
    ("PathAbleBackupFailed", "resolved", "upload"),
    ("PathAbleBackupNotVerifiedInS3", "firing", None),
]
if seen != expected:
    sys.exit(f"delivered {seen}, expected {expected}")
EOF
log "all $step notifications delivered as expected, and nothing else"
