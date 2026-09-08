#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../scripts/routing-policy.sh
source "$ROOT/scripts/routing-policy.sh"

assert_route() {
  local expected="$1"
  local labels="$2"
  local actual
  actual="$(rpgk_select_route "$labels")"
  if [[ "$actual" != "$expected" ]]; then
    echo "Expected route '$expected' but got '$actual' for labels: $labels" >&2
    exit 1
  fi
}

assert_fails() {
  local labels="$1"
  if rpgk_select_route "$labels" >/dev/null 2>&1; then
    echo "Expected conflicting labels to fail: $labels" >&2
    exit 1
  fi
}

assert_route $'gpt-5.6-terra\tmedium\tterra' ""
assert_route $'gpt-5.6-luna\tlow\tluna' "risk:mechanical"
assert_route $'gpt-5.6-terra\tmedium\tterra' "risk:normal"
assert_route $'gpt-5.6-sol\thigh\tsol' "risk:architecture"
assert_route $'gpt-6-astra\tmedium\tastra' "risk:end-to-end"

# Explicit model overrides risk classification.
assert_route $'gpt-5.6-terra\tmedium\tterra' $'risk:architecture\nmodel:terra'
assert_route $'gpt-6-astra\thigh\tastra' $'risk:normal\nmodel:astra\neffort:high'
assert_route $'gpt-5.6-luna\tmedium\tluna' $'model:luna\neffort:medium'

assert_fails $'model:luna\nmodel:terra'
assert_fails $'risk:mechanical\nrisk:architecture'
assert_fails $'effort:low\neffort:high'

echo "routing-policy-test: PASS"
