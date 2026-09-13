#!/usr/bin/env bash
# utils/build_omnibioai_ecosystem.sh
#
# Orchestrates a full ecosystem build:
#   1. Verify all repos are clean and on main (via ecosystem_status.sh)
#   2. Run coverage host
#   3. Generate report
#   4. Build and (re)start omnibioai-studio via docker compose
#
# Usage: bash omnibioai-utils/build_omnibioai_ecosystem.sh [root_dir]
# Default root: ~/Desktop/machine

set -euo pipefail

ROOT="${1:-$HOME/Desktop/machine}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
RESET='\033[0m'

# Resolve this script's directory so we can find sibling scripts regardless
# of where the caller invokes it from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Discord alert webhook -- same var + same source file (omnibioai-studio/.env)
# that omnibioai-studio's docker-compose stack already uses for GPU/known-issue
# alerts. An empty/missing value disables alerting gracefully, same as there.
if [ -z "${DISCORD_ALERT_WEBHOOK_URL:-}" ] && [ -f "$ROOT/omnibioai-studio/.env" ]; then
    DISCORD_ALERT_WEBHOOK_URL="$(grep -m1 '^DISCORD_ALERT_WEBHOOK_URL=' "$ROOT/omnibioai-studio/.env" | cut -d= -f2-)"
fi

step() {
    echo ""
    echo -e "${BOLD}==> $1${RESET}"
}

# Fire-and-forget Discord embed, mirroring control_center's notify(): title,
# description, error color, timestamp, footer. No-ops silently if the webhook
# isn't configured, and never fails the build if the alert itself fails.
discord_alert() {
    local message="$1" esc
    [ -n "${DISCORD_ALERT_WEBHOOK_URL:-}" ] || return 0
    command -v curl >/dev/null 2>&1 || return 0
    esc="${message//\\/\\\\}"
    esc="${esc//\"/\\\"}"
    esc="${esc//$'\n'/\\n}"
    curl -sS -m 10 -H "Content-Type: application/json" \
        -d "{\"username\":\"OmniBioAI\",\"embeds\":[{\"title\":\"omnibioai-ecosystem-build failed\",\"description\":\"${esc}\",\"color\":14893898,\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"footer\":{\"text\":\"$(hostname) · ${ROOT}\"}}]}" \
        "$DISCORD_ALERT_WEBHOOK_URL" >/dev/null 2>&1 || true
}

# Pass "docker" as $2 when the failure happened around a docker compose call
# so the current (possibly mixed) container state lands in the log right
# away, instead of requiring someone to SSH in and check.
fail() {
    echo -e "${RED}${BOLD}✗ $1${RESET}"
    if [ "${2:-}" = "docker" ]; then
        echo -e "${YELLOW}${BOLD}--- docker compose ps (current state) ---${RESET}"
        (cd "$STUDIO_DIR" && docker compose ps) 2>&1 || true
    fi
    discord_alert "$1"
    exit 1
}

ok() {
    echo -e "${GREEN}$1${RESET}"
}

# ---------------------------------------------------------------------------
# Step 1: Ecosystem status — must be all clean and all on main
# ---------------------------------------------------------------------------
step "Step 1/4: Checking ecosystem git status (${SCRIPT_DIR}/ecosystem_status.sh)"

STATUS_OUTPUT="$(bash "$SCRIPT_DIR/ecosystem_status.sh" "$ROOT" 2>&1)"
# Print it so the user sees the full table, but strip ANSI color codes first
# so the terminal doesn't show raw escape sequences if something goes odd.
echo "$STATUS_OUTPUT" | sed -E 's/\x1B\[[0-9;]*[a-zA-Z]//g'

PLAIN_OUTPUT="$(echo "$STATUS_OUTPUT" | sed -E 's/\x1B\[[0-9;]*[a-zA-Z]//g')"

DIRTY_COUNT="$(echo "$PLAIN_OUTPUT" | grep -E '^\s*Dirty\s*:' | grep -oE '[0-9]+' | head -1)"
NONMAIN_COUNT="$(echo "$PLAIN_OUTPUT" | grep -E '^\s*Non-main branch\s*:' | grep -oE '[0-9]+' | head -1)"

DIRTY_COUNT="${DIRTY_COUNT:-0}"
NONMAIN_COUNT="${NONMAIN_COUNT:-0}"

if [ "$DIRTY_COUNT" -ne 0 ] || [ "$NONMAIN_COUNT" -ne 0 ]; then
    echo ""
    fail "Ecosystem is not ready: ${DIRTY_COUNT} dirty repo(s), ${NONMAIN_COUNT} repo(s) not on main. Fix these before building."
fi

ok "✓ All repos clean and on main. Proceeding to Step 2."

# ---------------------------------------------------------------------------
# Step 2: Run coverage host
# ---------------------------------------------------------------------------
step "Step 2/4: Running coverage host (omnibioai-control-center/scripts/run_coverage_host.py)"

if ! python "$ROOT/omnibioai-control-center/scripts/run_coverage_host.py"; then
    fail "run_coverage_host.py failed. Aborting before generating report."
fi

ok "✓ Coverage host completed. Proceeding to Step 3."

# ---------------------------------------------------------------------------
# Step 3: Generate report
# ---------------------------------------------------------------------------
step "Step 3/4: Generating report (omnibioai-control-center/scripts/generate_report.py)"

if ! python "$ROOT/omnibioai-control-center/scripts/generate_report.py"; then
    fail "generate_report.py failed. Aborting before docker build."
fi

ok "✓ Report generated with no errors. Proceeding to Step 4."

# ---------------------------------------------------------------------------
# Step 4: Build and recreate omnibioai-studio
# ---------------------------------------------------------------------------
step "Step 4/4: Building and starting omnibioai-studio (docker compose)"

STUDIO_DIR="$ROOT/omnibioai-studio"
[ -d "$STUDIO_DIR" ] || fail "Studio directory not found: $STUDIO_DIR"

(
    cd "$STUDIO_DIR"
    docker compose build
    docker compose up -d --force-recreate
) || fail "Docker compose build/up failed for omnibioai-studio." docker

ok "✓ omnibioai-studio built and running."
echo ""
echo -e "${BOLD}${GREEN}All 4 steps completed successfully.${RESET}"