#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
expected="$(cd "$repo/.." && pwd)"
actual="$(env -u OMNIBIOAI_ROOT bash -c 'source "$1/ecosystem_path.sh"; printf "%s" "$OMNIBIOAI_ROOT"' _ "$repo")"
[[ "$actual" == "$expected" ]]
override="$(OMNIBIOAI_ROOT="/tmp/root with spaces" bash -c 'source "$1/ecosystem_path.sh"; printf "%s" "$OMNIBIOAI_ROOT"' _ "$repo")"
[[ "$override" == "/tmp/root with spaces" ]]
