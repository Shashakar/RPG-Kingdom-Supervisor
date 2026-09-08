#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/GH-456"
cd "$TMP/GH-456"

bash "$ROOT/scripts/before-run-guard.sh"

touch .symphony-attempt-complete
if bash "$ROOT/scripts/before-run-guard.sh" >/dev/null 2>&1; then
  echo "before-run guard allowed a second worker lifetime" >&2
  exit 1
fi

rm .symphony-attempt-complete
bash "$ROOT/scripts/before-run-guard.sh"

echo "worker-lifetime-guard-test: PASS"
