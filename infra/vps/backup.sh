#!/bin/sh
# Logical backup of the whole PathAble database on this host, and its copy off it.
#
#   backup.sh [label]      label: scheduled (the timer), pre-deploy, pre-restore,
#                          manual (default)
#
# A full `pg_dump --format=custom` of the `pathable` database: schema, the map
# facts, the place index and the dataset-lifecycle evidence, with ownership. It
# is what restore.sh takes. The file name carries the UTC time, the label and
# the revision that was serving. Beside it go a .sha256 file and a manifest
# (.manifest.json): the file's SHA-256, the release, and what the database held
# — its revision, active datasets and every table's row count.
#
# The off-host copy (offsite.py): once ROOT/offsite.env exists, the dump and its
# manifest are uploaded to S3 and verified there, and only then is the backup
# done. PATHABLE_BACKUP_OFFSITE says whether that is needed:
#   auto      (default) required if offsite.env exists; otherwise local only,
#             and the last line says so
#   required  fail unless the off-host copy is verified: the systemd timer,
#             deploy.sh's pre-deploy backup and restore.sh's safety backup
#             before it replaces production, whether or not offsite.env exists
#   skip      local only, deliberately: a deploy during an S3 outage. Said
#             first (offsite_skipped) and last
# When the copy fails, the local backup stays and this exits non-zero. Every
# later run retries each dump that has a manifest and no off-host receipt.
#
# Local retention: files older than PATHABLE_BACKUP_KEEP_DAYS (14) are deleted,
# but the newest three are always kept, whatever their age, so a host that
# stopped backing up never deletes its last good copy. S3 keeps its copies for
# 30 days by the bucket's lifecycle rule; nothing here deletes anything there.
#
# Output is one JSON object per line, and never includes a credential.

set -eu

ROOT="${PATHABLE_ROOT:-/srv/pathable}"
DEST="${PATHABLE_BACKUP_DIR:-/var/backups/pathable}"
KEEP_DAYS="${PATHABLE_BACKUP_KEEP_DAYS:-14}"
CONTAINER="${PATHABLE_DB_CONTAINER:-pathable-db-1}"
OFFSITE="${PATHABLE_BACKUP_OFFSITE:-auto}"
RELEASE="$ROOT/release.env"
HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL="${1:-manual}"

# event NAME key=value...  Plain numbers are written as numbers, everything
# else as a string.
event() {
    event_name="$1"; shift
    fields=""
    for pair in "$@"; do
        key="${pair%%=*}"; value="${pair#*=}"
        if printf '%s' "$value" | grep -Eq '^[0-9]+(\.[0-9]+)?$'; then
            fields="$fields,\"$key\":$value"
        else
            fields="$fields,\"$key\":\"$(printf '%s' "$value" | sed 's/\\/\\\\/g; s/"/\\"/g')\""
        fi
    done
    printf '{"ts":"%s","component":"backup","event":"%s"%s}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$event_name" "$fields"
}
fail() { event backup_failed label="$LABEL" "$@"; exit 1; }
now() { date +%s.%N; }
elapsed() { awk -v a="$1" -v b="$2" 'BEGIN { printf "%.1f", b - a }'; }

case "$LABEL" in
    *[!a-z0-9-]*|"") LABEL=invalid; fail reason="label must be lowercase letters, digits and hyphens" ;;
esac
case "$OFFSITE" in
    auto) if [ -f "$ROOT/offsite.env" ]; then OFFSITE=required; else OFFSITE=local-only; fi ;;
    required|skip) ;;
    *) fail reason="PATHABLE_BACKUP_OFFSITE must be auto, required or skip" ;;
esac
[ "$OFFSITE" != "skip" ] || event offsite_skipped label="$LABEL" \
    warning="PATHABLE_BACKUP_OFFSITE=skip: this backup will exist on this host only"

revision="$(sed -n 's/^PATHABLE_REVISION=//p' "$RELEASE" 2>/dev/null | cut -c1-12)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
name="pathable-${stamp}-${LABEL}-${revision:-unknown}.dump"
mkdir -p "$DEST"
umask 077

started="$(now)"
# Written under a temporary name and renamed only after pg_dump succeeds, so a
# half-written file is never mistaken for a backup.
if ! docker exec "$CONTAINER" pg_dump -U postgres -d pathable --format=custom --compress=6 \
    > "$DEST/$name.partial"; then
    rm -f "$DEST/$name.partial"
    fail stage=pg_dump file="$name"
fi
mv "$DEST/$name.partial" "$DEST/$name"
dump_seconds="$(elapsed "$started" "$(now)")"
( cd "$DEST" && sha256sum "$name" > "$name.sha256" )

# A dump that pg_restore cannot list is not a backup.
entries="$(docker exec -i "$CONTAINER" pg_restore --list < "$DEST/$name" | grep -c 'TABLE DATA')" \
    || fail stage=pg_restore-list file="$name" reason="pg_restore lists no table data"

python3 "$HERE/offsite.py" manifest "$DEST/$name" --label "$LABEL" --started "$started_at" \
    --dump-seconds "$dump_seconds" --container "$CONTAINER" \
    || fail stage=manifest file="$name"
event local_backup_written file="$name" bytes="$(stat -c %s "$DEST/$name")" \
    sha256="$(cut -d' ' -f1 "$DEST/$name.sha256")" tables_with_data="$entries" seconds="$dump_seconds"

offsite_status=0
if [ "$OFFSITE" = "required" ]; then
    if python3 "$HERE/offsite.py" push "$DEST/$name"; then
        # Then anything an earlier run could not copy.
        python3 "$HERE/offsite.py" push-pending "$DEST" || offsite_status=1
    else
        offsite_status=1
    fi
fi

# Retention: never below three files.
total="$(find "$DEST" -maxdepth 1 -name 'pathable-*.dump' | wc -l)"
find "$DEST" -maxdepth 1 -name 'pathable-*.dump' -mtime +"$KEEP_DAYS" -printf '%T@ %p\n' \
    | sort -n | while read -r _ old; do
        [ "$total" -le 3 ] && break
        rm -f "$old" "$old.sha256" "$old.manifest.json" "$old.offsite.json"
        event expired file="$(basename "$old")"
        total=$((total - 1))
    done

case "$OFFSITE" in
    required)
        [ "$offsite_status" = "0" ] \
            || fail file="$name" offsite=failed \
                reason="the local backup was written, but its off-host copy is not verified"
        event backup_complete label="$LABEL" file="$name" offsite=verified ;;
    skip)
        event backup_complete label="$LABEL" file="$name" offsite="skipped (PATHABLE_BACKUP_OFFSITE=skip)" ;;
    local-only)
        event backup_complete label="$LABEL" file="$name" offsite="not configured: local copy only" ;;
esac
