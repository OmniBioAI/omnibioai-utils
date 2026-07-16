#!/usr/bin/env bash
set -uo pipefail

# ---- CONFIG ----
NEW_ORG="omnibioai"
GH_TOKEN="${GH_TOKEN:?Set GH_TOKEN env var with a PAT that has write:packages + repo scope, and org admin rights}"

urlencode() {
  local s="$1"
  s="${s//\//%2F}"
  echo "$s"
}

echo "Listing all container packages in org: $NEW_ORG"

page=1
> ./org_packages.txt
while : ; do
  resp=$(curl -s -H "Authorization: Bearer $GH_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    -w '\n%{http_code}' \
    "https://api.github.com/orgs/${NEW_ORG}/packages?package_type=container&per_page=100&page=${page}")
  http_code=$(echo "$resp" | tail -n1)
  body=$(echo "$resp" | sed '$d')

  if [ "$http_code" != "200" ]; then
    echo "!! API error (HTTP $http_code) listing packages: $body"
    exit 1
  fi

  count=$(echo "$body" | jq 'length')
  [ "$count" -eq 0 ] && break

  # name + current visibility, one per line, tab-separated
  echo "$body" | jq -r '.[] | "\(.name)\t\(.visibility)"' >> ./org_packages.txt
  page=$((page+1))
done

TOTAL=$(wc -l < ./org_packages.txt)
echo "Found $TOTAL packages in $NEW_ORG."

made_public=0
already_public=0
failed=0

while IFS=$'\t' read -r name visibility; do
  [ -z "$name" ] && continue
  enc_name=$(urlencode "$name")

  if [ "$visibility" = "public" ]; then
    echo "  [ok] $name already public"
    already_public=$((already_public+1))
    continue
  fi

  echo "  setting $name -> public"
  resp=$(curl -s -w '\n%{http_code}' -X PATCH \
    -H "Authorization: Bearer $GH_TOKEN" \
    -H "Accept: application/vnd.github.package-deletes-preview+json" \
    -H "Content-Type: application/json" \
    "https://api.github.com/orgs/${NEW_ORG}/packages/container/${enc_name}" \
    -d '{"visibility":"public"}')
  http_code=$(echo "$resp" | tail -n1)
  resp_body=$(echo "$resp" | sed '$d')

  if [ "$http_code" = "200" ] || [ "$http_code" = "204" ]; then
    made_public=$((made_public+1))
  else
    echo "  !! failed to set $name public (HTTP $http_code): $resp_body"
    failed=$((failed+1))
  fi
done < ./org_packages.txt

echo ""
echo "==== Summary ===="
echo "Already public:  $already_public"
echo "Made public:     $made_public"
echo "Failed:          $failed"
echo ""
echo "Review: https://github.com/orgs/${NEW_ORG}/packages"