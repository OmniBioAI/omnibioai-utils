#!/usr/bin/env bash
# Shared portable ecosystem path resolution for utility scripts.
# OMNIBIOAI_ROOT wins; otherwise derive the ecosystem root from this checkout.
if [[ -z "${OMNIBIOAI_UTILS_DIR:-}" ]]; then
  OMNIBIOAI_UTILS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
omni_resolve_root() {
  if [[ -n "${OMNIBIOAI_ROOT:-}" ]]; then printf '%s\n' "$OMNIBIOAI_ROOT"; return; fi
  printf '%s\n' "$(cd "$OMNIBIOAI_UTILS_DIR/.." && pwd)"
}
OMNIBIOAI_ROOT="$(omni_resolve_root)"
export OMNIBIOAI_ROOT
