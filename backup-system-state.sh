#!/usr/bin/env bash
# backup-system-state.sh — Daily backup of spark-70f0's non-git system state
#
# Backs up: every real .env file across the ecosystem repos, a fresh
# crontab export, /etc/cloudflared's tunnel config + credentials, and the
# 5 custom systemd unit files. None of this lives in git, so losing it
# means the box's role in the ecosystem can't be reconstructed even though
# the application code itself is all pushed elsewhere.
#
# Deliberately excludes .ssh/, .aws/, .kube/, .gnupg/, .npmrc, .pypirc, and
# the GitHub Actions runner's .credentials/.runner/.credentials_rsaparams --
# too high blast-radius for an unattended daily job, and the runner files
# aren't restorable from backup anyway (would need re-registration
# regardless).
#
# Output is GPG-encrypted (AES256, symmetric) using the same passphrase
# file and reasoning as backup-config.sh: BACKUP_DIR resolves onto the
# exFAT OmniBioAI-SIFs mount (fmask=0000,dmask=0000), which can't enforce
# Unix permissions, and this archive contains plaintext credentials.
#
# The encrypted blob is also pushed to a private GitHub repo
# (man4ish/omnibioai-system-backups) as the off-site copy -- if this
# machine and the external drive are lost together, a local-only .gpg
# copy doesn't help.
#
# Reading /etc/cloudflared/cert.pem, its tunnel credentials JSON, and
# /etc/systemd/system/dgx-dashboard-admin.service requires root. This
# script relies on a narrow NOPASSWD sudoers exception (tar + chown,
# fixed paths only, no wildcards) -- see /etc/sudoers.d/backup-system-state.
# Every other file in scope is world-readable and copied directly.
#
# Intended to be run from cron, e.g.:
#   0 4 * * * /home/manish/Desktop/machine/omnibioai-utils/backup-system-state.sh >> /home/manish/Desktop/machine/work/backups/omnibioai-system-state-backup.log 2>&1
#
# Reviewed and active in control-center's cron registry (GET /cron/jobs,
# id "system-state-backup", paused: false). Verified via 3 successful
# manual runs on 2026-07-28 across two sessions (archive created, GPG
# encryption succeeded, off-site push to the private GitHub repo landed)
# before the crontab line was left unpaused. First real unattended
# cron firing is the following 04:00.

set -euo pipefail

MACHINE_DIR="/home/manish/Desktop/machine"
BACKUP_DIR="${MACHINE_DIR}/work/backups/system-state"
REPO_DIR="/home/manish/.omnibioai-system-backups-repo"
PASSPHRASE_FILE="/home/manish/.omnibioai-config-backup-passphrase"
RETENTION_DAYS=30
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ARCHIVE_NAME="system-state_${TIMESTAMP}.tar.gz.gpg"

log() {
    echo "[INFO] $(date -Iseconds) $*"
}

err() {
    echo "[ERROR] $(date -Iseconds) $*" >&2
}

if [ ! -f "$PASSPHRASE_FILE" ]; then
    err "Passphrase file not found at ${PASSPHRASE_FILE}."
    err "This must be the same passphrase already used by backup-config.sh -- do not create a second one."
    exit 1
fi

if [ ! -d "${REPO_DIR}/.git" ]; then
    err "Off-site repo clone not found at ${REPO_DIR}."
    err "Run once: git clone https://github.com/man4ish/omnibioai-system-backups.git ${REPO_DIR}"
    exit 1
fi

mkdir -p "$BACKUP_DIR"

log "Starting system-state backup -> ${BACKUP_DIR}/${ARCHIVE_NAME}"

SCRATCH_ARCHIVE=""
STAGING_DIR=$(mktemp -d)
cleanup() {
    rm -rf "$STAGING_DIR"
    [ -n "$SCRATCH_ARCHIVE" ] && rm -f "$SCRATCH_ARCHIVE"
}
trap cleanup EXIT

# ── 1. .env files across the ecosystem repos ────────────────────────
ENV_FILES=(
    ".env"
    "omnibioai-auth/.env"
    "omnibioai-control-center/frontend/cc-ui/.env"
    "omnibioai-lims/.env"
    "omnibioai-lims/.env_cloud"
    "omnibioai-lims/frontend/.env.production"
    "omnibioai-rag/.env"
    "omnibioai-studio/.env"
    "omnibioai-studio/.env.web"
    "omnibioai-tes/frontend/tes-ui/.env.production"
    "omnibioai-tool-images/omnibioai-tool-runtime/.env.azure"
    "omnibioai-tool-runtime/.env.azure"
)

mkdir -p "${STAGING_DIR}/env"
FOUND_ENV=0
for f in "${ENV_FILES[@]}"; do
    src="${MACHINE_DIR}/${f}"
    if [ -f "$src" ]; then
        dest="${STAGING_DIR}/env/${f}"
        mkdir -p "$(dirname "$dest")"
        cp "$src" "$dest"
        FOUND_ENV=$((FOUND_ENV + 1))
    else
        err "Expected .env file missing, skipping: ${f}"
    fi
done
log "Collected ${FOUND_ENV}/${#ENV_FILES[@]} .env files"

# ── 2. Fresh crontab export ──────────────────────────────────────────
crontab -l > "${STAGING_DIR}/crontab.txt" 2>/dev/null || {
    err "crontab -l returned nothing/failed -- continuing with an empty snapshot"
    : > "${STAGING_DIR}/crontab.txt"
}

# ── 3. cloudflared: world-readable files directly, root-only files
#      via the narrow sudoers exception ──────────────────────────────
mkdir -p "${STAGING_DIR}/cloudflared" "${STAGING_DIR}/systemd"
for f in config.yml config.yml.bak back_config.yml; do
    cp "/etc/cloudflared/${f}" "${STAGING_DIR}/cloudflared/${f}"
done

ROOT_SCRATCH_DIR="${BACKUP_DIR}/.root-scratch"
ROOT_SCRATCH_TAR="${ROOT_SCRATCH_DIR}/root-only-files.tar.gz"
mkdir -p "$ROOT_SCRATCH_DIR"
rm -f "$ROOT_SCRATCH_TAR"

# Must match /etc/sudoers.d/backup-system-state's whitelisted command
# argument-for-argument -- fixed paths only, no wildcards.
sudo /usr/bin/tar -czf "$ROOT_SCRATCH_TAR" \
    -C /etc/cloudflared cert.pem 00090181-2f99-468c-967d-79a1b81080c8.json \
    -C /etc/systemd/system dgx-dashboard-admin.service
sudo /usr/bin/chown manish:manish "$ROOT_SCRATCH_TAR"
chmod 600 "$ROOT_SCRATCH_TAR"

tar -xzf "$ROOT_SCRATCH_TAR" -C "${STAGING_DIR}/cloudflared" cert.pem 00090181-2f99-468c-967d-79a1b81080c8.json
tar -xzf "$ROOT_SCRATCH_TAR" -C "${STAGING_DIR}/systemd" dgx-dashboard-admin.service
rm -f "$ROOT_SCRATCH_TAR"

# ── 4. Remaining (world-readable) systemd units ──────────────────────
for f in cloudflared.service cloudflared-update.service \
         "actions.runner.man4ish-omnibioai-dev-docker.dgx-runner.service" \
         omnicellexplorer-shiny.service; do
    cp "/etc/systemd/system/${f}" "${STAGING_DIR}/systemd/${f}"
done

log "Staged $(find "$STAGING_DIR" -type f | wc -l) files"

# ── 5. tar + GPG encrypt (AES256, symmetric) ─────────────────────────
SCRATCH_ARCHIVE=$(mktemp)
tar -czf "$SCRATCH_ARCHIVE" -C "$STAGING_DIR" .

gpg --batch --yes --pinentry-mode loopback \
    --passphrase-file "$PASSPHRASE_FILE" \
    --symmetric --cipher-algo AES256 \
    -o "${BACKUP_DIR}/${ARCHIVE_NAME}" \
    "$SCRATCH_ARCHIVE"

SIZE=$(du -h "${BACKUP_DIR}/${ARCHIVE_NAME}" | cut -f1)
log "Backup complete — ${SIZE} written to ${BACKUP_DIR}/${ARCHIVE_NAME} (GPG-encrypted, AES256)"

# ── 6. Push to private off-site GitHub repo ──────────────────────────
cp "${BACKUP_DIR}/${ARCHIVE_NAME}" "${REPO_DIR}/${ARCHIVE_NAME}"

find "${REPO_DIR}" -maxdepth 1 -name 'system-state_*.tar.gz.gpg' -mtime "+${RETENTION_DAYS}" -print -delete | while read -r f; do
    log "Pruned old off-site backup: $(basename "$f")"
done

(
    cd "$REPO_DIR"
    git add -A
    if ! git diff --cached --quiet; then
        git commit -m "Backup ${TIMESTAMP}" --quiet
        git push origin HEAD:main --quiet
        log "Pushed ${ARCHIVE_NAME} to private off-site repo"
    else
        log "Nothing new to push to off-site repo"
    fi
)

# ── 7. Prune local archives older than RETENTION_DAYS ─────────────────
find "$BACKUP_DIR" -maxdepth 1 -name 'system-state_*.tar.gz.gpg' -mtime "+${RETENTION_DAYS}" -print -delete | while read -r f; do
    log "Pruned old local backup: $f"
done

log "Done."
