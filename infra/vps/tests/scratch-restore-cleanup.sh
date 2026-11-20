#!/bin/sh
# Regression: a scratch restore leaves no container, network or volume behind.
#
# Until 2026-11-17, `restore.sh --into scratch` removed its PostGIS container
# with `docker rm -f` and no -v. The image declares /var/lib/postgresql/data a
# volume, so every run left its restored database on the host: the run of
# 2026-10-15 left volume c4dd6515… (449.7 MB, a full copy of that day's
# database) unattached for a month. Its INT trap also ran the cleanup and then
# carried on with the restore.
#
# This runs the real restore.sh, with real Docker and the real PostGIS image,
# twice, on a tiny dump that restores but holds no dataset:
#   1. to the end, which fails verification ("no active OpenStreetMap dataset");
#   2. stopped with SIGTERM while its scratch server starts.
# After each, the scratch container, network and volume must all be gone and
# the exit status must say what happened. A run that succeeds takes the same
# EXIT trap; making one here would need a whole dataset and the API image.
#
#   sh infra/vps/tests/scratch-restore-cleanup.sh        (needs a Docker daemon)

set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
VPS="$(dirname "$HERE")"
IMAGE="postgis/postgis:17-3.5"
SCRATCH_DB="pathable-restore-check-db"
SCRATCH_NETWORK="pathable-restore-check"
MAKER="pathable-restore-test-dump-maker"
WORK="$(mktemp -d)"
DUMP="$WORK/pathable-20261117T000000Z-test-000000000000.dump"
trap 'docker rm -f -v "$MAKER" >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT

fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull -q "$IMAGE" >/dev/null
# The precondition of the bug: a container of this image gets an anonymous
# volume. Without it this test could not tell a leak from no volume at all.
docker image inspect -f '{{json .Config.Volumes}}' "$IMAGE" | grep -q '/var/lib/postgresql/data' \
    || fail "$IMAGE no longer declares its data directory a volume; this test would prove nothing"

# A tiny dump, from a server whose data directory is a tmpfs, so making it
# leaves no volume either.
docker run -d --name "$MAKER" --tmpfs /var/lib/postgresql/data -e POSTGRES_PASSWORD=test-only \
    "$IMAGE" >/dev/null
until docker exec "$MAKER" psql -h 127.0.0.1 -U postgres -Atc 'select 1' >/dev/null 2>&1; do
    sleep 1
done
docker exec "$MAKER" psql -q -U postgres -c 'create database pathable'
docker exec "$MAKER" psql -q -U postgres -d pathable -c "
    create table alembic_version (version_num varchar(32) primary key);
    insert into alembic_version values ('test');
    create table dataset_versions (id uuid primary key, status text, source_type text);"
docker exec "$MAKER" pg_dump -U postgres -d pathable --format=custom > "$DUMP"
docker rm -f -v "$MAKER" >/dev/null

# What restore.sh reads from the host it runs on.
mkdir -p "$WORK/root/secrets"
printf 'PATHABLE_DB_PASSWORD=test-only\n' > "$WORK/root/secrets/pathable.env"
printf 'PATHABLE_REVISION=test\n' > "$WORK/root/release.env"

volumes() { docker volume ls -q | sort > "$1"; }
nothing_left() {
    volumes "$WORK/volumes.after"
    leaked="$(comm -13 "$WORK/volumes.before" "$WORK/volumes.after")"
    [ -z "$leaked" ] || fail "$1: volume(s) left behind: $leaked"
    [ -z "$(docker ps -aq --filter "name=^${SCRATCH_DB}\$")" ] || fail "$1: the scratch container is still there"
    [ -z "$(docker network ls -q --filter "name=^${SCRATCH_NETWORK}\$")" ] || fail "$1: the scratch network is still there"
    pass "$1: no container, network or volume left behind"
}
restore() {
    PATHABLE_ROOT="$WORK/root" PATHABLE_BACKUP_DIR="$WORK" sh "$VPS/restore.sh" --into scratch --dump "$DUMP"
}

# --- 1. to the end: restored, then refused by verification --------------------
volumes "$WORK/volumes.before"
status=0
restore > "$WORK/run1.log" 2>&1 || status=$?
cat "$WORK/run1.log"
[ "$status" = "1" ] || fail "run 1: exit status $status, expected 1 (verification refused the dump)"
grep -q 'pg_restore took' "$WORK/run1.log" || fail "run 1: stopped before the restore itself"
grep -q 'no active OpenStreetMap dataset' "$WORK/run1.log" || fail "run 1: failed somewhere other than verification"
nothing_left "run 1 (verification failed)"
grep -q 'removed the scratch container and its volume' "$WORK/run1.log" \
    || fail "run 1: did not report removing its volume"

# --- 2. stopped while its scratch server starts ---------------------------------
volumes "$WORK/volumes.before"
# Started directly, not through the function, so $! is restore.sh itself and
# the signal reaches it rather than a subshell around it.
PATHABLE_ROOT="$WORK/root" PATHABLE_BACKUP_DIR="$WORK" sh "$VPS/restore.sh" --into scratch --dump "$DUMP" \
    > "$WORK/run2.log" 2>&1 &
pid=$!
until [ -n "$(docker ps -q --filter "name=^${SCRATCH_DB}\$")" ]; do
    kill -0 "$pid" 2>/dev/null || { cat "$WORK/run2.log"; fail "run 2: ended before its scratch server started"; }
    sleep 0.2
done
kill -TERM "$pid"
status=0
wait "$pid" || status=$?
cat "$WORK/run2.log"
[ "$status" = "143" ] || fail "run 2: exit status $status after SIGTERM, expected 143"
if grep -q 'restoring into a throwaway container' "$WORK/run2.log"; then
    fail "run 2: carried on restoring after SIGTERM"
fi
nothing_left "run 2 (stopped by SIGTERM)"
