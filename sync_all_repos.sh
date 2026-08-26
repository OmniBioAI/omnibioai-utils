
#!/usr/bin/env bash

set -u

ROOT="${1:-$HOME/Desktop/machine}"

REPOS=(
    "omnibioai-api-gateway"
    "omnibioai-auth"
    "omnibioai-billing"
    "omnibioai-control-center"
    "omnibioai-data"
    "omnibioai-db-init"
    "omnibioai-design-tokens"
    "omnibioai-dev-docker"
    "omnibioai-dev-hub"
    "omnibioai-docs"
    "omnibioai-hpc-policy-engine"
    "omnibioai-iam-client"
    "omnibioai-landing"
    "omnibioai-launcher"
    "omnibioai-lims"
    "omnibioai-model-registry"
    "omnibioai-policy-engine"
    "omnibioai-rag"
    "omnibioai-sdk"
    "omnibioai-security-audit"
    "omnibioai-security-sdk"
    "omnibioai-studio"
    "omnibioai-system-backups"
    "omnibioai-tes"
    "omnibioai-tool-images"
    "omnibioai-tool-runtime"
    "omnibioai-toolserver"
    "omnibioai-ui"
    "omnibioai-usage-client"
    "omnibioai-utils"
    "omnibioai-videos"
    "omnibioai-work"
    "omnibioai-workbench"
    "omnibioai-workflow-bundles"
)

echo
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║             OmniBioAI — Sync All Repositories                  ║"
echo "║             Root: $ROOT"
echo "║             Target: origin/main"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo

if [[ ! -d "$ROOT" ]]; then
    echo "ERROR: Root directory does not exist: $ROOT"
    exit 1
fi

cd "$ROOT" || exit 1

total=0
synced=0
already=0
updated=0
missing=0
dirty=0
failed=0

for repo in "${REPOS[@]}"; do

    ((total++))

    echo "─────────────────────────────────────────────────────────────────────"
    echo "[$total/${#REPOS[@]}] $repo"

    if [[ ! -d "$repo/.git" ]]; then
        echo "  ✗ Repository not found locally"
        ((missing++))
        continue
    fi

    cd "$ROOT/$repo" || {
        echo "  ✗ Cannot enter repository"
        ((failed++))
        continue
    }

    # Verify origin
    if ! git remote get-url origin >/dev/null 2>&1; then
        echo "  ✗ No origin remote"
        ((failed++))
        continue
    fi

    echo "  Origin: $(git remote get-url origin)"

    # Verify current branch
    branch=$(git branch --show-current)

    if [[ "$branch" != "main" ]]; then
        echo "  → Current branch: ${branch:-DETACHED}"

        if [[ -n "$(git status --porcelain)" ]]; then
            echo "  ⚠ Dirty working tree — cannot switch safely"
            ((dirty++))
            continue
        fi

        if ! git switch main >/dev/null 2>&1; then
            echo "  ✗ Could not switch to main"
            ((failed++))
            continue
        fi

        echo "  ✓ Switched to main"
    fi

    # Never touch uncommitted changes
    if [[ -n "$(git status --porcelain)" ]]; then
        echo "  ⚠ Dirty working tree — skipped"
        ((dirty++))
        continue
    fi

    # Fetch remote
    echo "  → Fetching origin..."
    if ! git fetch origin --prune; then
        echo "  ✗ Fetch failed"
        ((failed++))
        continue
    fi

    # Verify origin/main
    if ! git show-ref --verify --quiet refs/remotes/origin/main; then
        echo "  ✗ origin/main does not exist"
        ((failed++))
        continue
    fi

    LOCAL=$(git rev-parse main)
    REMOTE=$(git rev-parse origin/main)

    # Already synchronized
    if [[ "$LOCAL" == "$REMOTE" ]]; then
        echo "  ✓ Already synchronized"
        ((already++))
        ((synced++))
        continue
    fi

    # Determine whether local is behind
    if git merge-base --is-ancestor "$LOCAL" "$REMOTE"; then

        echo "  → Local main is behind origin/main"
        echo "  → Fast-forwarding..."

        if git merge --ff-only origin/main >/dev/null 2>&1; then
            echo "  ✓ Updated to origin/main"
            ((updated++))
            ((synced++))
        else
            echo "  ✗ Fast-forward failed"
            ((failed++))
        fi

    elif git merge-base --is-ancestor "$REMOTE" "$LOCAL"; then

        echo "  ⚠ Local main is ahead of origin/main"
        echo "    Local : $LOCAL"
        echo "    Remote: $REMOTE"
        echo "  ⚠ No changes made"
        ((failed++))

    else

        echo "  ⚠ Branches have diverged"
        echo "    Local : $LOCAL"
        echo "    Remote: $REMOTE"
        echo "  ⚠ No changes made"
        ((failed++))

    fi

done

cd "$ROOT" || exit 1

echo
echo "═════════════════════════════════════════════════════════════════════"
echo "SUMMARY"
echo "═════════════════════════════════════════════════════════════════════"
echo "  Repositories expected : ${#REPOS[@]}"
echo "  Repositories scanned  : $total"
echo "  Synchronized          : $synced"
echo "    Already current     : $already"
echo "    Updated             : $updated"
echo "  Missing locally       : $missing"
echo "  Dirty / skipped       : $dirty"
echo "  Failed / divergent    : $failed"
echo

if [[ "$missing" -eq 0 && "$dirty" -eq 0 && "$failed" -eq 0 ]]; then
    echo "✓ ALL 34 LOCAL OMNIBIOAI REPOSITORIES ARE SYNCHRONIZED."
else
    echo "⚠ Some repositories require attention."
fi

echo

