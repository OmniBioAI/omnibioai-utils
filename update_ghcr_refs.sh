#!/usr/bin/env bash
set -uo pipefail

# ---- CONFIG ----
OLD_REF="ghcr.io/man4ish"
NEW_REF="ghcr.io/omnibioai"
SEARCH_DIR="${1:-.}"
REPORT_FILE="./ghcr_ref_matches.txt"

# Directories to skip (build artifacts, VCS internals, dependencies)
EXCLUDE_DIRS=(.git node_modules .venv venv __pycache__ dist build .cache work release obsolete)

EXCLUDE_ARGS=()
for d in "${EXCLUDE_DIRS[@]}"; do
  EXCLUDE_ARGS+=(--exclude-dir="$d")
done

echo "Searching for '${OLD_REF}' references under: $SEARCH_DIR"
echo ""

# grep: -r recursive, -n line numbers, -I skip binary files, -F literal string match
grep -rnIF "${EXCLUDE_ARGS[@]}" --exclude='*.log' "$OLD_REF" "$SEARCH_DIR" > "$REPORT_FILE" 2>/dev/null

MATCH_COUNT=$(wc -l < "$REPORT_FILE")
FILE_COUNT=$(cut -d: -f1 "$REPORT_FILE" | sort -u | wc -l)

if [ "$MATCH_COUNT" -eq 0 ]; then
  echo "No references to '$OLD_REF' found. Nothing to do."
  exit 0
fi

echo "Found $MATCH_COUNT matches across $FILE_COUNT files:"
echo ""
cat "$REPORT_FILE"
echo ""
echo "Full list also saved to: $REPORT_FILE"
echo ""

if [ "${2:-}" != "--apply" ]; then
  echo "=== DRY RUN ONLY - nothing was changed ==="
  echo "Review the matches above. To actually replace '$OLD_REF' -> '$NEW_REF' in all"
  echo "these files, re-run:"
  echo "  bash $0 \"$SEARCH_DIR\" --apply"
  exit 0
fi

echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
echo "About to REPLACE '$OLD_REF' -> '$NEW_REF' in $FILE_COUNT files."
echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
echo "Type YES to proceed, anything else to abort:"
read -r confirmation
if [ "$confirmation" != "YES" ]; then
  echo "Aborted. Nothing was changed."
  exit 0
fi

changed=0
while IFS= read -r file; do
  [ -z "$file" ] && continue
  # .bak backup before editing, in case you need to revert manually
  cp "$file" "${file}.bak"
  sed -i "s|${OLD_REF}|${NEW_REF}|g" "$file"
  echo "  updated: $file (backup: ${file}.bak)"
  changed=$((changed+1))
done < <(cut -d: -f1 "$REPORT_FILE" | sort -u)

echo ""
echo "==== Summary ===="
echo "Files updated: $changed"
echo ""
echo "Backups (.bak) were created alongside each modified file. If everything"
echo "looks correct, clean them up with:"
echo "  find \"$SEARCH_DIR\" -name '*.bak' -delete"
echo ""
echo "If this is a git repo, review the diff before committing:"
echo "  git diff"