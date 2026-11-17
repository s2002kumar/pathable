#!/bin/sh
# Restore a backup.sh dump, and prove it is the database it claims to be.
#
#   restore.sh --into scratch [--dump FILE] [--route]   non-destructive: a
#       throwaway PostGIS container, restored, verified, then removed with its
#       volume. Production is not touched. This is the routine proof that the
#       backups restore. --route also starts the release's API image against
#       the restored database and checks the recorded journey's answer.
#   restore.sh --into production --dump FILE --yes   disaster recovery: takes a
#       safety backup, stops the API, replaces the `pathable` database with the
#       dump, verifies it, and starts the API again.
#
# Without --dump, the newest file in the backup directory is used. A dump
# fetched from S3 (offsite.py fetch) restores the same way.
#
# Verification, in both modes:
#   * the Alembic revision, and that every map-fact and place-index table loaded
#   * the active OpenStreetMap dataset's node and segment counts against the
#     counts its own dataset_versions row records
#   * the place index's entry count against the counts its build row records
#   * the sealed-dataset triggers, and that the application role is not a
#     superuser
#   * when the dump has a manifest beside it (DUMP.manifest.json): its SHA-256,
#     and every table's row count, the revision and the active datasets against
#     what the manifest recorded when the backup was taken
#   * the content checksum recomputed from the restored rows by the release's
#     own API image (`pathable datasets checksum`), which must match the one
#     recorded when the dataset was verified
#
# The dump's PostGIS and pg_trgm extensions are created by the superuser before
# the restore, exactly as db-init/20-pathable.sh does on a fresh volume, and the
# dump's own CREATE EXTENSION entries are skipped. The database password reaches
# containers through files in a private temporary directory and psql's standard
# input, never a command line.

set -eu

ROOT="${PATHABLE_ROOT:-/srv/pathable}"
DEST="${PATHABLE_BACKUP_DIR:-/var/backups/pathable}"
SECRETS="$ROOT/secrets/pathable.env"
RELEASE="$ROOT/release.env"
PROD_DB="pathable-db-1"
PROD_API="pathable-api-1"
PROD_NETWORK="pathable_data"
SCRATCH_DB="pathable-restore-check-db"
SCRATCH_API="pathable-restore-check-api"
SCRATCH_NETWORK="pathable-restore-check"
POSTGIS_IMAGE="postgis/postgis:17-3.5"
# What a graph-loaded API takes (1,129 MiB at its peak, 2026-11-16) with room
# left for production beside it; --route refuses to start below this.
ROUTE_MIN_AVAILABLE_MIB="${PATHABLE_ROUTE_CHECK_MIN_MIB:-1500}"
HERE="$(cd "$(dirname "$0")" && pwd)"

INTO=""; DUMP=""; YES=0; ROUTE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --into) INTO="$2"; shift 2 ;;
        --dump) DUMP="$2"; shift 2 ;;
        --yes) YES=1; shift ;;
        --route) ROUTE=1; shift ;;
        *) sed -n '2,14p' "$0"; exit 2 ;;
    esac
done

log() { printf '%s restore: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"; }
die() { log "$1"; exit 1; }
now() { date +%s.%N; }
elapsed() { awk -v a="$1" -v b="$2" 'BEGIN { printf "%.1f", b - a }'; }

case "$INTO" in scratch|production) ;; *) die "--into scratch|production is required" ;; esac
[ "$ROUTE" = "0" ] || [ "$INTO" = "scratch" ] || die "--route is for --into scratch only"
if [ -z "$DUMP" ]; then
    DUMP="$(find "$DEST" -maxdepth 1 -name 'pathable-*.dump' -printf '%T@ %p\n' | sort -n | tail -n 1 | cut -d' ' -f2-)"
fi
if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then die "no dump to restore"; fi
DUMP_SHA256="$(sha256sum "$DUMP" | cut -d' ' -f1)"
if [ -f "$DUMP.sha256" ]; then
    ( cd "$(dirname "$DUMP")" && sha256sum --quiet -c "$(basename "$DUMP").sha256" ) \
        || die "checksum mismatch: $DUMP is not the file backup.sh wrote"
fi
MANIFEST="$DUMP.manifest.json"
if [ -f "$MANIFEST" ]; then
    recorded="$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["sha256"])' "$MANIFEST")" \
        || die "cannot read the manifest $MANIFEST"
    [ "$recorded" = "$DUMP_SHA256" ] || die "checksum mismatch: $DUMP is not the file its manifest describes"
fi
log "dump $(basename "$DUMP") ($(du -h "$DUMP" | cut -f1), sha256 $(printf '%s' "$DUMP_SHA256" | cut -c1-16)…)"
[ -f "$MANIFEST" ] && log "manifest $(basename "$MANIFEST") describes it"

# shellcheck disable=SC1090
. "$SECRETS"
REVISION="$(sed -n 's/^PATHABLE_REVISION=//p' "$RELEASE")"
# The release's own image runs the checksum. PATHABLE_CLI_IMAGE overrides it for
# a release whose image cannot start the CLI: images built before the libexpat1
# fix in services/api/Dockerfile (see docs/deployment/VPS.md).
API_IMAGE="${PATHABLE_CLI_IMAGE:-pathable-api:$REVISION}"

# Private (0700) until the script ends, however it ends.
WORK="$(mktemp -d)"
SCRATCH_VOLUMES=""
LEAKED=0
remove_scratch() {
    # -v: without it `docker rm` keeps a container's anonymous volumes, and the
    # PostGIS image declares its data directory one. Every scratch run before
    # 2026-11-17 left its restored database behind that way: 449.7 MB from the
    # run of 2026-10-15 sat unattached on the host for a month.
    docker rm -f -v "$SCRATCH_API" >/dev/null 2>&1 || true
    docker rm -f -v "$SCRATCH_DB" >/dev/null 2>&1 || true
    docker network rm "$SCRATCH_NETWORK" >/dev/null 2>&1 || true
    for volume in $SCRATCH_VOLUMES; do
        if docker volume inspect "$volume" >/dev/null 2>&1; then
            log "  FAIL  scratch volume $volume is still on this host"
            LEAKED=1
        else
            log "  removed the scratch container and its volume $volume"
        fi
    done
    SCRATCH_VOLUMES=""
}
on_exit() {
    status=$?
    [ "$INTO" != "scratch" ] || remove_scratch
    rm -rf "$WORK"
    [ "$LEAKED" = "0" ] || status=1
    exit "$status"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

volumes_of() {
    docker inspect -f '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}} {{end}}{{end}}' "$1"
}

# An env file the release's image reads: how DATABASE_URL reaches it.
database_env() {
    printf 'DATABASE_URL=postgresql+psycopg://pathable_app:%s@%s:5432/pathable\n' \
        "$PATHABLE_DB_PASSWORD" "$1" > "$WORK/database.env"
}

# --- the restore itself, into database `pathable` of a running container ------
recreate_and_restore() {
    container="$1"
    docker exec -i "$container" psql -v ON_ERROR_STOP=1 -q -U postgres -d postgres <<SQL
DROP DATABASE IF EXISTS pathable WITH (FORCE);
CREATE DATABASE pathable OWNER pathable_app TEMPLATE template0 ENCODING 'UTF8' LOCALE 'C';
\connect pathable
CREATE EXTENSION postgis;
CREATE EXTENSION pg_trgm;
SQL
    docker exec -i "$container" sh -c 'cat > /tmp/pathable-restore.dump' < "$DUMP"
    docker exec "$container" sh -c \
        "pg_restore --list /tmp/pathable-restore.dump | grep -v ' EXTENSION ' > /tmp/pathable-restore.list"
    restore_started="$(now)"
    docker exec "$container" pg_restore -U postgres -d pathable \
        --exit-on-error --single-transaction \
        --use-list /tmp/pathable-restore.list /tmp/pathable-restore.dump
    docker exec "$container" rm -f /tmp/pathable-restore.dump /tmp/pathable-restore.list
    log "  pg_restore took $(elapsed "$restore_started" "$(now)")s"
    docker exec "$container" psql -q -U postgres -d pathable -c "vacuum analyze" >/dev/null
}

# --- verification -----------------------------------------------------------
FAILED=0
check() {
    if [ "$2" = "$3" ]; then log "  ok    $1: $2"; else log "  FAIL  $1: got $2, expected $3"; FAILED=1; fi
}
verify() {
    container="$1"; network="$2"; host="$3"
    q() { docker exec "$container" psql -U postgres -d pathable -At -c "$1"; }
    log "verifying"
    log "  alembic revision $(q 'select version_num from alembic_version')"
    active="$(q "select id from dataset_versions where status='active' and source_type='osm'")"
    [ -n "$active" ] || { log "  FAIL  no active OpenStreetMap dataset"; FAILED=1; return; }
    log "  active dataset $active"
    check "nodes" "$(q "select count(*) from graph_nodes where dataset_version_id='$active'")" \
        "$(q "select node_count from dataset_versions where id='$active'")"
    check "segments" "$(q "select count(*) from graph_edges where dataset_version_id='$active'")" \
        "$(q "select edge_count from dataset_versions where id='$active'")"
    check "place index entries" "$(q "select count(*) from gazetteer_entries")" \
        "$(q "select coalesce(sum(place_count + address_count + street_count), 0) from gazetteer_builds")"
    check "sealed-dataset triggers" \
        "$(q "select count(*) from pg_trigger where tgname in ('graph_nodes_immutable_once_sealed','graph_edges_immutable_once_sealed','dataset_versions_lifecycle')")" "3"
    check "role pathable_app is not a superuser" \
        "$(q "select rolsuper from pg_roles where rolname='pathable_app'")" "f"
    log "  database size $(q "select pg_size_pretty(pg_database_size('pathable'))")"

    if [ -f "$MANIFEST" ]; then
        if python3 "$HERE/offsite.py" verify-restore "$MANIFEST" --container "$container"; then
            log "  ok    every table's rows, the revision and the active datasets match the manifest"
        else
            log "  FAIL  the restored database differs from what its manifest recorded"
            FAILED=1
        fi
    fi

    # The checksum is recomputed by the release's own code, in its own container,
    # connected as the application role — the path the API itself reads through.
    log "  recomputing the content checksum with $API_IMAGE"
    database_env "$host"
    checksum_started="$(now)"
    if docker run --rm --network "$network" --memory 1g --env-file "$WORK/database.env" \
        "$API_IMAGE" pathable datasets checksum "$active"; then
        log "  ok    content checksum matches ($(elapsed "$checksum_started" "$(now)")s)"
    else
        log "  FAIL  content checksum does not match, or none is recorded"
        FAILED=1
    fi
}

# --- the recorded journey, answered from the restored database (--route) ------
route_check() {
    available="$(awk '/^MemAvailable:/ { print int($2 / 1024) }' /proc/meminfo)"
    if [ "$available" -lt "$ROUTE_MIN_AVAILABLE_MIB" ]; then
        log "  FAIL  route check not run: $available MiB available, $ROUTE_MIN_AVAILABLE_MIB needed beside production"
        FAILED=1
        return
    fi
    log "routing over the restored database ($API_IMAGE on the internal scratch network, $available MiB available)"
    {
        echo "ENVIRONMENT=production"
        echo "APP_VERSION=restore-check"
        echo "API_HOST=0.0.0.0"
        echo "API_PORT=8000"
        echo "ALLOWED_ORIGINS=https://restore-check.invalid"
        echo "LOG_LEVEL=WARNING"
        echo "LOG_FORMAT=json"
        echo "GRAPH_PRELOAD_REGIONS=waterloo"
        echo "GEOCODING_PROVIDER=local"
        printf 'DATABASE_URL=postgresql+psycopg://pathable_app:%s@scratch:5432/pathable\n' "$PATHABLE_DB_PASSWORD"
    } > "$WORK/api.env"
    api_started="$(now)"
    # Never the host's first choice to kill: if memory ran out, this goes first.
    docker run -d --name "$SCRATCH_API" --network "$SCRATCH_NETWORK" \
        --memory 1536m --oom-score-adj 1000 --cap-drop ALL --security-opt no-new-privileges:true \
        --env-file "$WORK/api.env" "$API_IMAGE" python -m pathable_api >/dev/null
    SCRATCH_VOLUMES="$SCRATCH_VOLUMES $(volumes_of "$SCRATCH_API")"
    deadline=$(( $(date +%s) + 300 ))
    until docker exec "$SCRATCH_API" python -c 'import urllib.request, sys; sys.exit(0 if urllib.request.urlopen("http://127.0.0.1:8000/api/v1/health/ready", timeout=5).status == 200 else 1)' >/dev/null 2>&1; do
        if [ "$(date +%s)" -ge "$deadline" ] \
            || [ "$(docker inspect -f '{{.State.Running}}' "$SCRATCH_API" 2>/dev/null)" != "true" ]; then
            log "  FAIL  the scratch API did not become ready"
            docker logs --tail 5 "$SCRATCH_API" 2>&1 | cut -c1-300 || true
            FAILED=1
            return
        fi
        sleep 2
    done
    log "  scratch API ready after $(elapsed "$api_started" "$(now)")s, peak $(( $(docker exec "$SCRATCH_API" cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo 0) / 1048576 )) MiB"
    if answer="$(docker exec -i "$SCRATCH_API" python - "$(cat "$HERE/load/journey.json")" <<'PY'
import json
import sys
import urllib.request

journey = json.loads(sys.argv[1])
body = json.dumps({k: journey[k] for k in ("region", "origin", "destination", "profile")}).encode()
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/v1/routes/compare",
    data=body,
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=60) as response:
    route = json.load(response)


def facts(leg):
    return [round(leg["distance_m"], 1), leg["stairway_count"]]


want = journey["expected"]
got = {
    "standard": facts(route["standard_route"]),
    "accessible": facts(route["accessible_route"]),
    "dataset_id": route["dataset"]["dataset_id"],
    "ml_predictions_used": route["ml_predictions_used"],
}
expected = {
    "standard": [want["standard"]["distance_m"], want["standard"]["stairway_count"]],
    "accessible": [want["accessible"]["distance_m"], want["accessible"]["stairway_count"]],
    "dataset_id": want["dataset_id"],
    "ml_predictions_used": False,
}
print(json.dumps(got))
sys.exit(0 if got == expected else 1)
PY
)"; then
        log "  ok    the recorded journey, from the restored database: $answer"
    else
        log "  FAIL  the recorded journey answered differently: ${answer:-no answer}"
        FAILED=1
    fi
}

if [ "$INTO" = "scratch" ]; then
    remove_scratch   # whatever an interrupted earlier run left
    docker network create --internal "$SCRATCH_NETWORK" >/dev/null
    scratch_password="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    total_started="$(now)"
    docker run -d --name "$SCRATCH_DB" --network "$SCRATCH_NETWORK" --network-alias scratch \
        --memory 768m --shm-size 256m --oom-score-adj 1000 \
        -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD="$scratch_password" -e POSTGRES_DB=postgres \
        -e POSTGRES_INITDB_ARGS="--encoding=UTF8 --locale=C" \
        "$POSTGIS_IMAGE" >/dev/null
    SCRATCH_VOLUMES="$(volumes_of "$SCRATCH_DB")"
    # TCP, not the socket: the image's first-boot server listens on the socket only.
    until docker exec "$SCRATCH_DB" psql -h 127.0.0.1 -U postgres -d postgres -Atc 'select 1' >/dev/null 2>&1; do
        sleep 1
    done
    # The application role, with production's password, so the checksum and the
    # API connect exactly as they do there. The password goes in on stdin.
    {
        printf '%s\n' "\\set pw '$(printf '%s' "$PATHABLE_DB_PASSWORD" | sed "s/'/''/g")'"
        echo "CREATE ROLE pathable_app LOGIN PASSWORD :'pw'"
        echo "    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION NOINHERIT;"
    } | docker exec -i "$SCRATCH_DB" psql -v ON_ERROR_STOP=1 -q -U postgres -d postgres
    log "restoring into a throwaway container ($SCRATCH_DB); production is not touched"
    recreate_and_restore "$SCRATCH_DB"
    verify "$SCRATCH_DB" "$SCRATCH_NETWORK" scratch
    if [ "$ROUTE" = "1" ] && [ "$FAILED" = "0" ]; then
        route_check
    fi
    log "scratch restore and verification took $(elapsed "$total_started" "$(now)")s in all"
else
    [ "$YES" = "1" ] || die "--into production replaces the live database; add --yes"
    log "safety backup of the database being replaced"
    "$HERE/backup.sh" pre-restore
    total_started="$(now)"
    docker stop "$PROD_API" >/dev/null
    log "API stopped; restoring into $PROD_DB"
    recreate_and_restore "$PROD_DB"
    verify "$PROD_DB" "$PROD_NETWORK" db
    if [ "$FAILED" != "0" ]; then
        die "VERIFICATION FAILED — the API was left stopped. The safety backup is the newest pre-restore file."
    fi
    docker start "$PROD_API" >/dev/null
    until docker exec "$PROD_API" python -c 'import urllib.request,sys; sys.exit(0 if urllib.request.urlopen("http://127.0.0.1:8000/api/v1/health/ready", timeout=5).status==200 else 1)' >/dev/null 2>&1; do
        sleep 1
    done
    log "API ready again; outage $(elapsed "$total_started" "$(now)")s from stop to ready"
fi

[ "$FAILED" = "0" ] || die "VERIFICATION FAILED"
log "restore verified"
