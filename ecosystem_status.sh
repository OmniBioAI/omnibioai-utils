#!/usr/bin/env bash
# utils/ecosystem_status.sh
# Check git status for all repos in the OmniBioAI ecosystem
# Usage: bash utils/ecosystem_status.sh [root_dir]
# Default root: ~/Desktop/machine

ROOT="${1:-$HOME/Desktop/machine}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

# Counters
total=0
clean=0
dirty=0
non_main=0

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║           OmniBioAI Ecosystem Git Status                         ║${RESET}"
echo -e "${BOLD}║           Root: ${ROOT}${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════════╝${RESET}"
echo ""
printf "%-35s %-20s %-10s %s\n" "REPOSITORY" "BRANCH" "STATUS" "DETAILS"
echo -e "${DIM}─────────────────────────────────────────────────────────────────────${RESET}"

for dir in "$ROOT"/*/; do
    [ -d "$dir/.git" ] || continue
    name=$(basename "$dir")
    case "$name" in
        omnibioai|db-init|obsolete|utils|data|work) continue ;;
    esac

    total=$((total + 1))

    branch=$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null)
    [ -z "$branch" ] && branch="unknown"

    uncommitted=$(git -C "$dir" status --porcelain 2>/dev/null)
    unpushed=$(git -C "$dir" log @{u}.. --oneline 2>/dev/null | wc -l | tr -d ' ')
    untracked=$(echo "$uncommitted" | grep "^??" | wc -l | tr -d ' ')
    modified=$(echo "$uncommitted" | grep -v "^??" | grep -v "^$" | wc -l | tr -d ' ')

    if [ "$branch" != "main" ] && [ "$branch" != "master" ]; then
        non_main=$((non_main + 1))
        branch_display="${YELLOW}${branch}${RESET}"
    else
        branch_display="${GREEN}${branch}${RESET}"
    fi

    if [ -z "$uncommitted" ] && [ "$unpushed" -eq 0 ]; then
        status="${GREEN}✓ clean${RESET}"
        clean=$((clean + 1))
    else
        status="${RED}✗ dirty${RESET}"
        dirty=$((dirty + 1))
        details=""
        [ "$modified" -gt 0 ]  && details+="${RED}${modified} modified${RESET} "
        [ "$untracked" -gt 0 ] && details+="${YELLOW}${untracked} untracked${RESET} "
        [ "$unpushed" -gt 0 ]  && details+="${CYAN}${unpushed} unpushed${RESET} "
    fi

    printf "%-35s " "$name"
    printf "%-30b " "$branch_display"
    printf "%-20b " "$status"
    printf "%b\n" "${details:-}"
    details=""
done

echo -e "${DIM}─────────────────────────────────────────────────────────────────────${RESET}"
echo ""
echo -e "${BOLD}Summary:${RESET}"
echo -e "  Total repos scanned : ${BOLD}${total}${RESET}"
echo -e "  Clean               : ${GREEN}${clean}${RESET}"
if [ "$dirty" -gt 0 ]; then
    echo -e "  Dirty               : ${RED}${dirty}${RESET}"
else
    echo -e "  Dirty               : ${GREEN}${dirty}${RESET}"
fi
if [ "$non_main" -gt 0 ]; then
    echo -e "  Non-main branch     : ${YELLOW}${non_main}${RESET}"
else
    echo -e "  Non-main branch     : ${GREEN}${non_main}${RESET}"
fi
echo ""

# Dirty repo details
if [ "$dirty" -gt 0 ]; then
    echo -e "${BOLD}Dirty repo details:${RESET}"
    for dir in "$ROOT"/*/; do
        [ -d "$dir/.git" ] || continue
        name=$(basename "$dir")
        case "$name" in
            omnibioai|db-init|obsolete|utils|data|work) continue ;;
        esac
        uncommitted=$(git -C "$dir" status --porcelain 2>/dev/null)
        unpushed=$(git -C "$dir" log @{u}.. --oneline 2>/dev/null | wc -l | tr -d ' ')
        if [ -n "$uncommitted" ] || [ "$unpushed" -gt 0 ]; then
            echo -e "\n  ${BOLD}${name}${RESET}"
            [ -n "$uncommitted" ] && git -C "$dir" status --short 2>/dev/null | head -10 | \
                while read -r line; do echo "    $line"; done
            [ "$unpushed" -gt 0 ] && echo -e "    ${CYAN}↑ ${unpushed} unpushed commit(s)${RESET}"
        fi
    done
    echo ""
fi

# Non-main branches
if [ "$non_main" -gt 0 ]; then
    echo -e "${BOLD}Non-main branches:${RESET}"
    for dir in "$ROOT"/*/; do
        [ -d "$dir/.git" ] || continue
        name=$(basename "$dir")
        case "$name" in
            omnibioai|db-init|obsolete|utils|data|work) continue ;;
        esac
        branch=$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null)
        if [ "$branch" != "main" ] && [ "$branch" != "master" ] && [ -n "$branch" ]; then
            echo -e "  ${YELLOW}${name}${RESET} → ${branch}"
        fi
    done
    echo ""
fi