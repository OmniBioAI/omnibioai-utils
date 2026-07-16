#!/usr/bin/env bash
set -uo pipefail

# ---- CONFIG ----
GH_TOKEN="${GH_TOKEN:?Set GH_TOKEN env var with a PAT that has delete:packages scope}"
PACKAGES_FILE="./old_packages.txt"   # produced by verify_migration.sh

# ---- OPTIONS ----
DRY_RUN=1   # default: dry run. Pass --confirm-delete to actually delete.
if [ "${1:-}" = "--confirm-delete" ]; then
  DRY_RUN=0
fi

urlencode() {
  local s="$1"
  s="${s//\//%2F}"
  echo "$s"
}

if [ ! -s "$PACKAGES_FILE" ]; then
  echo "!! $PACKAGES_FILE not found or empty. Run verify_migration.sh first to generate it."
  exit 1
fi

TOTAL=$(wc -l < "$PACKAGES_FILE")
echo "Found $TOTAL packages listed in $PACKAGES_FILE (under your personal ghcr.io namespace)."

if [ "$DRY_RUN" -eq 1 ]; then
  echo ""
  echo "=== DRY RUN MODE ==="
  echo "No packages will be deleted. This just shows what WOULD be deleted."
  echo "Review the list below carefully."
  echo ""
else
  echo ""
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  echo "!! THIS WILL PERMANENTLY DELETE $TOTAL PACKAGES. THIS CANNOT BE UNDONE. !!"
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  echo ""
  echo "Type DELETE (all caps) to proceed, anything else to abort:"
  read -r confirmation
  if [ "$confirmation" != "DELETE" ]; then
    echo "Aborted. Nothing was deleted."
    exit 0
  fi
  echo "Confirmed. Proceeding with deletion..."
  echo ""
fi

deleted=0
failed=0
skipped_dry=0

while IFS= read -r pkg; do
  [ -z "$pkg" ] && continue
  enc_pkg=$(urlencode "$pkg")

  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [would delete] ghcr.io/man4ish/${pkg}"
    skipped_dry=$((skipped_dry+1))
    continue
  fi

  resp=$(curl -s -w '\n%{http_code}' -X DELETE \
    -H "Authorization: Bearer $GH_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/user/packages/container/${enc_pkg}")
  http_code=$(echo "$resp" | tail -n1)
  body=$(echo "$resp" | sed '$d')

  if [ "$http_code" = "204" ]; then
    echo "  [deleted] $pkg"
    deleted=$((deleted+1))
  else
    echo "  !! failed to delete $pkg (HTTP $http_code): $body"
    failed=$((failed+1))
  fi
done < "$PACKAGES_FILE"

echo ""
echo "==== Summary ===="
if [ "$DRY_RUN" -eq 1 ]; then
  echo "Would delete: $skipped_dry packages"
  echo ""
  echo "Nothing was actually deleted. To actually delete, run:"
  echo "  bash $0 --confirm-delete"
else
  echo "Deleted: $deleted"
  echo "Failed:  $failed"
fi