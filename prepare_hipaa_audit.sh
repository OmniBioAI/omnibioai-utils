i#!/usr/bin/env bash
set -uo pipefail

ROOT="${1:-$HOME/Desktop/machine}"

echo
echo "=============================================================="
echo " OmniBioAI — HIPAA Audit Baseline Preparation"
echo " Root: $ROOT"
echo "=============================================================="
echo

if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git is required."
    exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
    echo "WARNING: gh is not installed."
    echo "Merged-PR detection will be limited."
    echo
fi

cd "$ROOT" || exit 1

UPDATED=()
ALREADY_MAIN=()
MERGED_CLEANED=()
UNMERGED=()
DIRTY=()
ERRORS=()

# Repositories shown by ecosystem_status.sh
REPOS=(
    omnibioai
    omnibioai-api-gateway
    omnibioai-auth
    omnibioai-billing
    omnibioai-control-center
    omnibioai-data
    omnibioai-db-init
    omnibioai-design-tokens
    omnibioai-dev-docker
    omnibioai-dev-hub
    omnibioai-docs
    omnibioai-ecosystem
    omnibioai-hpc-policy-engine
    omnibioai-iam-client
    omnibioai-landing
    omnibioai-launcher
    omnibioai-lims
    omnibioai-model-registry
    omnibioai-policy-engine
    omnibioai-rag
    omnibioai-sdk
    omnibioai-security-audit
    omnibioai-security-sdk
    omnibioai-studio
    omnibioai-system-backups
    omnibioai-tes
    omnibioai-tool-images
    omnibioai-tool-runtime
    omnibioai-toolserver
    omnibioai-ui
    omnibioai-usage-client
    omnibioai-utils
    omnibioai-videos
    omnibioai-workflow-bundles
)

is_dirty() {
    [[ -n "$(git status --porcelain 2>/dev/null)" ]]
}

echo "Step 1/5 — Fetching remotes"
echo "--------------------------------------------------------------"

for repo in "${REPOS[@]}"; do
    if [[ ! -d "$ROOT/$repo/.git" ]]; then
        echo "SKIP: $repo (not a git repository)"
        continue
    fi

    cd "$ROOT/$repo" || continue

    echo "Fetching $repo ..."
    if ! git fetch origin --prune >/dev/null 2>&1; then
        echo "  ERROR: fetch failed"
        ERRORS+=("$repo: fetch failed")
    fi
done

cd "$ROOT"

echo
echo "Step 2/5 — Inspecting current branches"
echo "--------------------------------------------------------------"

for repo in "${REPOS[@]}"; do
    dir="$ROOT/$repo"

    [[ -d "$dir/.git" ]] || continue

    cd "$dir" || continue

    branch="$(git branch --show-current 2>/dev/null || true)"

    if [[ "$branch" == "main" ]]; then
        ALREADY_MAIN+=("$repo")
        continue
    fi

    echo
    echo "Repository : $repo"
    echo "Branch     : $branch"

    if is_dirty; then
        echo "  STATUS   : DIRTY — will NOT switch branches"
        DIRTY+=("$repo [$branch]")
        continue
    fi

    # Determine GitHub remote.
    remote="$(git remote get-url origin 2>/dev/null || true)"

    if [[ -z "$remote" ]]; then
        echo "  STATUS   : no origin remote"
        ERRORS+=("$repo: no origin remote")
        continue
    fi

    # Only attempt GitHub PR detection when gh exists.
    if command -v gh >/dev/null 2>&1; then

        pr_state="$(
            gh pr list \
                --head "$branch" \
                --state all \
                --json number,state,title,url \
                --limit 20 2>/dev/null \
            || true
        )"

        if [[ -z "$pr_state" || "$pr_state" == "[]" ]]; then
            echo "  PR       : none found"
            echo "  ACTION   : leaving branch untouched"
            UNMERGED+=("$repo [$branch] — no PR found")
            continue
        fi

        # Extract the first merged PR, if one exists.
        merged_pr="$(
            printf '%s' "$pr_state" |
            python3 -c '
import json,sys
try:
    data=json.load(sys.stdin)
except Exception:
    sys.exit(0)
for p in data:
    if p.get("state") == "MERGED":
        print(
            f"{p.get(\"number\", \"?\")}|"
            f"{p.get(\"title\", \"\")}|"
            f"{p.get(\"url\", \"\")}"
        )
        break
' 2>/dev/null || true
        )"

        if [[ -n "$merged_pr" ]]; then
            IFS='|' read -r pr_number pr_title pr_url <<< "$merged_pr"

            echo "  PR       : #$pr_number MERGED"
            echo "  URL      : $pr_url"
            echo "  ACTION   : switching to main"

            if git checkout main >/dev/null 2>&1; then

                if git pull --ff-only origin main >/dev/null 2>&1; then
                    echo "  RESULT   : main synchronized"
                    MERGED_CLEANED+=("$repo [$branch] → main (PR #$pr_number)")
                    UPDATED+=("$repo")
                else
                    echo "  ERROR    : main pull --ff-only failed"
                    ERRORS+=("$repo: main fast-forward failed")
                fi

            else
                echo "  ERROR    : could not checkout main"
                ERRORS+=("$repo: checkout main failed")
            fi

        else
            echo "  PR       : no merged PR found"
            echo "  ACTION   : leaving branch untouched"
            UNMERGED+=("$repo [$branch]")
        fi

    else
        echo "  ACTION   : gh unavailable; leaving branch untouched"
        UNMERGED+=("$repo [$branch] — gh unavailable")
    fi
done

cd "$ROOT"

echo
echo "Step 3/5 — Updating repositories already on main"
echo "--------------------------------------------------------------"

for repo in "${REPOS[@]}"; do
    dir="$ROOT/$repo"

    [[ -d "$dir/.git" ]] || continue

    cd "$dir" || continue

    branch="$(git branch --show-current 2>/dev/null || true)"

    [[ "$branch" == "main" ]] || continue

    if is_dirty; then
        echo "SKIP: $repo — dirty, refusing to modify"
        DIRTY+=("$repo [main]")
        continue
    fi

    echo "Updating $repo ..."

    if git pull --ff-only origin main >/dev/null 2>&1; then
        UPDATED+=("$repo")
    else
        echo "  ERROR: could not fast-forward"
        ERRORS+=("$repo: main pull failed")
    fi
done

cd "$ROOT"

echo
echo "Step 4/5 — Final safety scan"
echo "--------------------------------------------------------------"

FINAL_DIRTY=()
FINAL_NON_MAIN=()

for repo in "${REPOS[@]}"; do
    dir="$ROOT/$repo"

    [[ -d "$dir/.git" ]] || continue

    cd "$dir" || continue

    branch="$(git branch --show-current 2>/dev/null || true)"

    if is_dirty; then
        FINAL_DIRTY+=("$repo [$branch]")
    fi

    if [[ "$branch" != "main" ]]; then
        FINAL_NON_MAIN+=("$repo [$branch]")
    fi
done

cd "$ROOT"

echo
echo "=============================================================="
echo " FINAL HIPAA AUDIT BASELINE STATUS"
echo "=============================================================="
echo

echo "Repositories on main:"
printf '  %s\n' "${ALREADY_MAIN[@]}" "${UPDATED[@]}" 2>/dev/null |
    sort -u |
    sed '/^$/d' |
    sed 's/^/  ✓ /'

echo

if ((${#FINAL_NON_MAIN[@]})); then
    echo "Non-main branches requiring review:"
    printf '  %s\n' "${FINAL_NON_MAIN[@]}" |
        sed 's/^/  ⚠ /'
else
    echo "Non-main branches: NONE"
fi

echo

if ((${#FINAL_DIRTY[@]})); then
    echo "Dirty repositories requiring manual review:"
    printf '  %s\n' "${FINAL_DIRTY[@]}" |
        sed 's/^/  ⚠ /'
else
    echo "Dirty repositories: NONE"
fi

echo

if ((${#ERRORS[@]})); then
    echo "Errors:"
    printf '  %s\n' "${ERRORS[@]}" |
        sed 's/^/  ✗ /'
else
    echo "Errors: NONE"
fi

echo
echo "=============================================================="
echo " Recommended next step"
echo "=============================================================="

if ((${#FINAL_DIRTY[@]} == 0 && ${#FINAL_NON_MAIN[@]} == 0)); then
    echo
    echo "✓ All repositories are clean and on main."
    echo
    echo "You can now run the final end-to-end HIPAA audit."
else
    echo
    echo "⚠ DO NOT start the final audit yet."
    echo
    echo "Review the remaining dirty/non-main repositories above."
    echo "Do NOT discard uncommitted work or merge unrelated branches"
    echo "merely to make the baseline clean."
fi

echo
echo "Done."
