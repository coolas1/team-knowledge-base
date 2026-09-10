#!/usr/bin/env bash
#
# Back up the LAN deployment's data before a redeploy: a Postgres dump plus a
# snapshot of the uploads named volume (the pre-cutover data loss was exactly
# an unbacked uploads volume).
#
# Written to <stable-dir>/backups/<short-sha>-<timestamp>/, keeping the most
# recent TKB_BACKUP_KEEP sets (default 5). Invoked by pipeline.sh immediately
# before the redeploy replaces the stack.
#
# SINGLE-OPERATOR RULE: podman here touches the deployment's containers and
# volumes. This script is run ONLY by the pipeline (or a deliberate restore
# drill) - never by hand in the dev checkout.
#
# Restore steps: see cicd/README.md ("Restore from a backup").
set -Eeuo pipefail

TKB_CICD_HOME="${TKB_CICD_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
deploy_env="$TKB_CICD_HOME/deploy.env"
backups_dir="$TKB_CICD_HOME/backups"
keep="${TKB_BACKUP_KEEP:-5}"
postgres_container="${TKB_POSTGRES_CONTAINER:-team-kb-postgres}"
uploads_volume="${TKB_UPLOADS_VOLUME:-team-kb_uploadsdata}"
pg_user="${POSTGRES_USER:-kb_user}"
pg_db="${POSTGRES_DB:-knowledge_base}"

log() { printf '[backup] %s\n' "$*"; }
die() { log "FAILED: $*"; exit 1; }

# deploy.env supplies POSTGRES_USER/DB without exporting the app config.
if [[ -f "$deploy_env" ]]; then
  pg_user="$(grep -E '^POSTGRES_USER=' "$deploy_env" | tail -1 | cut -d= -f2- || true)"
  pg_db="$(grep -E '^POSTGRES_DB=' "$deploy_env" | tail -1 | cut -d= -f2- || true)"
  pg_user="${pg_user:-kb_user}"
  pg_db="${pg_db:-knowledge_base}"
fi

# Optional first arg: the SHA the backup precedes (labels the directory).
sha_label="${1:-$(date -u +%Y%m%dT%H%M%SZ)}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$backups_dir/${sha_label}-${stamp}"

mkdir -p "$target"

if ! podman container exists "$postgres_container"; then
  die "postgres container $postgres_container not found (is the stack deployed?)"
fi

log "dumping Postgres ($pg_db as $pg_user) -> $target/postgres.sql.gz"
podman exec "$postgres_container" \
  pg_dump -U "$pg_user" -d "$pg_db" --clean --if-exists \
  | gzip > "$target/postgres.sql.gz" \
  || die "pg_dump failed"

if podman volume exists "$uploads_volume"; then
  log "snapshotting uploads volume $uploads_volume -> $target/uploads.tar.gz"
  podman volume export "$uploads_volume" \
    | gzip > "$target/uploads.tar.gz" \
    || die "uploads volume export failed"
else
  log "warning: uploads volume $uploads_volume not found; skipping uploads snapshot"
fi

cp "$deploy_env" "$target/deploy.env.snapshot" 2>/dev/null || true
printf '%s\n' "$sha_label" > "$target/commit"
df -h "$target" | tail -1 > "$target/disk-usage.txt" 2>/dev/null || true

# Retention: keep the most recent $keep sets (directory names sort by sha then
# timestamp; fall back to mtime ordering when the label is not a plain sha).
if [[ "$keep" =~ ^[0-9]+$ ]] && (( keep > 0 )); then
  while IFS= read -r old; do
    log "retention: removing $old"
    rm -rf "$old"
  done < <(
    find "$backups_dir" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
      | sort -rn | tail -n +$((keep + 1)) | cut -d' ' -f2-
  )
fi

log "backup complete: $target"
