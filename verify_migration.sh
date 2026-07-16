#!/usr/bin/env bash
set -uo pipefail

# ---- CONFIG ----
OLD_OWNER="man4ish"
NEW_ORG="omnibioai"
GH_TOKEN="${GH_TOKEN:?Set GH_TOKEN env var}"
REPORT_FILE="./migration_verification_report.txt"

urlencode() {
  local s="$1"
  s="${s//\//%2F}"
  echo "$s"
}

gh_api_get() {
  local url="$1"
  local resp http_code body
  resp=$(curl -s -H "Authorization: Bearer $GH_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    -w '\n%{http_code}' "$url")
  http_code=$(echo "$resp" | tail -n1)
  body=$(echo "$resp" | sed '$d')
  if [ "$http_code" != "200" ]; then
    return 1
  fi
  echo "$body"
}

list_packages() {
  # $1 = "users" or "orgs", $2 = owner name
  local scope="$1" owner="$2" page=1
  while : ; do
    resp=$(gh_api_get "https://api.github.com/${scope}/${owner}/packages?package_type=container&per_page=100&page=${page}") || { echo "!! failed listing packages for $owner" >&2; break; }
    count=$(echo "$resp" | jq 'length')
    [ "$count" -eq 0 ] && break
    echo "$resp" | jq -r '.[].name'
    page=$((page+1))
  done
}

list_tags() {
  # $1 = "users" or "orgs", $2 = owner, $3 = package name
  local scope="$1" owner="$2" pkg="$3" enc_pkg vpage=1
  enc_pkg=$(urlencode "$pkg")
  while : ; do
    resp=$(gh_api_get "https://api.github.com/${scope}/${owner}/packages/container/${enc_pkg}/versions?per_page=100&page=${vpage}") || break
    vcount=$(echo "$resp" | jq 'length')
    [ "$vcount" -eq 0 ] && break
    echo "$resp" | jq -r '.[].metadata.container.tags[]?'
    vpage=$((vpage+1))
  done
}

get_digest() {
  # $1 = full ref e.g. ghcr.io/man4ish/foo:latest
  docker manifest inspect "$1" 2>/dev/null | jq -r '.config.digest // .manifests[0].digest // empty' 2>/dev/null
}

echo "Listing packages under $OLD_OWNER (user) ..."
list_packages "users" "$OLD_OWNER" | sort -u > ./old_packages.txt
echo "Listing packages under $NEW_ORG (org) ..."
list_packages "orgs" "$NEW_ORG" | sort -u > ./new_packages.txt

OLD_COUNT=$(wc -l < ./old_packages.txt)
NEW_COUNT=$(wc -l < ./new_packages.txt)
echo "Old ($OLD_OWNER): $OLD_COUNT packages. New ($NEW_ORG): $NEW_COUNT packages."

> "$REPORT_FILE"
echo "=== Migration verification report ===" >> "$REPORT_FILE"
echo "Generated: $(date)" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

echo "--- Packages in $OLD_OWNER but MISSING entirely from $NEW_ORG ---" | tee -a "$REPORT_FILE"
comm -23 ./old_packages.txt ./new_packages.txt | tee -a "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

echo ""
echo "--- Checking tags + digests for packages present in both ---"
echo "--- Tag/digest mismatches ---" >> "$REPORT_FILE"

mismatch_count=0
checked_count=0

while IFS= read -r pkg; do
  [ -z "$pkg" ] && continue
  if ! grep -Fxq "$pkg" ./new_packages.txt; then
    continue   # already reported as missing above
  fi

  old_tags=$(list_tags "users" "$OLD_OWNER" "$pkg" | sort -u)
  new_tags=$(list_tags "orgs" "$NEW_ORG" "$pkg" | sort -u)

  missing_tags=$(comm -23 <(echo "$old_tags") <(echo "$new_tags"))

  if [ -n "$missing_tags" ]; then
    echo "  [$pkg] missing tags in $NEW_ORG: $(echo "$missing_tags" | tr '\n' ' ')" | tee -a "$REPORT_FILE"
    mismatch_count=$((mismatch_count+1))
  fi

  checked_count=$((checked_count+1))
  if [ $((checked_count % 20)) -eq 0 ]; then
    echo "  ... checked $checked_count packages so far"
  fi
done < ./old_packages.txt

echo ""
echo "==== Summary ===="
echo "Packages checked:        $checked_count"
echo "Packages with issues:    $mismatch_count"
echo "Full report: $REPORT_FILE"
echo ""
if [ "$mismatch_count" -eq 0 ] && [ "$(comm -23 ./old_packages.txt ./new_packages.txt | wc -l)" -eq 0 ]; then
  echo "✓ Every package and tag in $OLD_OWNER appears to also exist in $NEW_ORG."
  echo "  (Note: this checks tag presence, not byte-for-byte digest equality - see notes below)"
else
  echo "✗ Some packages/tags are missing from $NEW_ORG - review $REPORT_FILE before deleting anything."
fi
echo ""
echo "NOTE: This checks that the same TAGS exist in both places, which catches"
echo "the most common failure mode (a package or tag never got migrated)."
echo "It does not compare image digests byte-for-byte, since that requires"
echo "pulling manifests for every tag which is slow at this scale. If you want"
echo "that extra level of certainty for specific packages, run:"
echo '  docker manifest inspect ghcr.io/man4ish/<pkg>:<tag>'
echo '  docker manifest inspect ghcr.io/omnibioai/<pkg>:<tag>'
echo "and diff the digests manually."