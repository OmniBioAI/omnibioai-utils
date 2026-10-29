#!/usr/bin/env bash
# switch_to_main.sh — switch OmniBioAI repos back to main ONLY when their branch is already merged.
#
# "Merged" means any of:
#   ancestor : branch tip is contained in origin/main (normal merge / fast-forward)
#   content  : merging the branch into origin/main changes nothing (catches squash & rebase merges)
#   pr       : a merged GitHub PR contains the branch's current tip commit (needs `gh` auth);
#              commits added after the PR merged make the branch count as NOT merged
#
# Safe by default:
#   * DRY RUN unless --apply
#   * unmerged branches are never switched (listed at the end for you to merge/PR)
#   * git worktrees are skipped (they exist to hold that branch; main is checked out elsewhere)
#   * dirty repos are skipped unless --stash-dirty (stash incl. untracked; never discards)
#   * omnibioai-rag skipped unless --include-rag (out of scope per issue #35)
#   * main is only fast-forwarded; nothing is merged, reset or deleted; feature branches stay
#
# Usage:
#   bash switch_to_main.sh                  # preview
#   bash switch_to_main.sh --apply
#   bash switch_to_main.sh --apply --stash-dirty
set -uo pipefail

ROOT="${ROOT:-$HOME/Desktop/omnibioai-ecosystem}"
APPLY=0; STASH=0; INCLUDE_RAG=0
for a in "$@"; do
  case "$a" in
    --apply) APPLY=1 ;;
    --stash-dirty) STASH=1 ;;
    --include-rag) INCLUDE_RAG=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

run() { if ((APPLY)); then "$@"; else echo "    [dry-run] $*"; fi; }

HAVE_GH=0
command -v gh >/dev/null && gh auth status >/dev/null 2>&1 && HAVE_GH=1

# prints how the branch is merged (ancestor|content|pr) or nothing
merged_how() {
  local br=$1
  git merge-base --is-ancestor HEAD origin/main 2>/dev/null && { echo ancestor; return; }
  local t
  if t=$(git merge-tree --write-tree origin/main HEAD 2>/dev/null) &&
     [ "$(head -n1 <<<"$t")" = "$(git rev-parse 'origin/main^{tree}')" ]; then
    echo content; return
  fi
  if ((HAVE_GH)) && [ "$br" != DETACHED ]; then
    # a merged PR counts only if the local tip is a commit that PR actually merged
    local tip heads h
    tip=$(git rev-parse HEAD)
    heads=$(gh pr list --head "$br" --state merged --json headRefOid --jq '.[].headRefOid' 2>/dev/null)
    for h in $heads; do
      [ "$h" = "$tip" ] && { echo pr; return; }
      git merge-base --is-ancestor "$tip" "$h" 2>/dev/null && { echo pr; return; }
    done
  fi
}

((APPLY)) || echo "DRY RUN. Nothing will change. Re-run with --apply."
((HAVE_GH)) || echo "(gh not authenticated: PR-based merge check disabled; ancestor + content checks still run)"
switched=0; skipped=0; unmerged=()

for dir in "$ROOT"/omnibioai-*/; do
  repo=$(basename "$dir")
  [ -e "$dir/.git" ] || continue
  cd "$dir" || continue

  cur=$(git symbolic-ref --short -q HEAD || echo DETACHED)
  [ "$cur" = main ] && continue
  echo "── $repo  ($cur)"

  if [ "$repo" = omnibioai-rag ] && ((!INCLUDE_RAG)); then
    echo "    SKIP: RAG excluded"; ((skipped++)); continue
  fi
  if [ "$(git rev-parse --path-format=absolute --git-dir)" != "$(git rev-parse --path-format=absolute --git-common-dir)" ]; then
    echo "    SKIP: git worktree (keeps its own branch)"; ((skipped++)); continue
  fi

  git fetch -q origin 2>/dev/null || echo "    WARN: fetch failed; using local refs"
  if ! git show-ref -q --verify refs/remotes/origin/main; then
    echo "    SKIP: no origin/main"; ((skipped++)); continue
  fi

  how=$(merged_how "$cur")
  if [ -z "$how" ]; then
    echo "    NOT MERGED: staying on $cur"
    unmerged+=("$repo ($cur)"); ((skipped++)); continue
  fi
  echo "    merged into main ($how)"

  changes=$(git status --porcelain | wc -l)
  if ((changes > 0)); then
    if ((STASH)); then
      run git stash push -u -m "switch-to-main $(date +%F) from $cur"
      echo "    stashed $changes change(s). Restore: git stash pop (on main) or on $cur"
    else
      echo "    SKIP: dirty ($changes changes); commit them or use --stash-dirty"; ((skipped++)); continue
    fi
  fi

  if git show-ref -q --verify refs/heads/main; then
    run git switch -q main
  else
    run git switch -q -c main --track origin/main
  fi
  run git pull -q --ff-only || echo "    WARN: local main diverged from origin/main; left as is"
  ((APPLY)) && echo "    OK: now on $(git branch --show-current)"
  ((switched++))
done

echo
echo "Switched: $switched   Skipped: $skipped   $( ((APPLY)) || echo '(dry run)')"
if ((${#unmerged[@]})); then
  echo "Not merged into main yet (merge or open a PR first):"
  printf '  - %s\n' "${unmerged[@]}"
fi