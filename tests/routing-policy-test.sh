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
    echo "Expected conflicting/invalid labels to fail: $labels" >&2
    exit 1
  fi
}

assert_route $'gpt-6-luna\tmedium\tluna' ""
assert_route $'gpt-6-luna\tlow\tluna' "risk:mechanical"
assert_route $'gpt-6-luna\tmedium\tluna' "risk:normal"
assert_route $'gpt-6-sol\tmedium\tsol' "risk:investigative"
assert_route $'gpt-6-sol\thigh\tsol' "risk:architecture"
assert_route $'gpt-6-sol\thigh\tsol' "risk:end-to-end"

# Explicit model overrides risk classification and reviewer routing hints.
assert_route $'gpt-6-luna\tmedium\tluna' $'model:luna\neffort:medium'
assert_route $'gpt-6-sol\tmedium\tsol' $'risk:mechanical\nmodel:sol\neffort:medium'
assert_route $'gpt-6-sol\thigh\tsol' $'risk:normal\nsymphony:rework\nrepair-route:sol'
assert_route $'gpt-6-luna\tlow\tluna' $'risk:architecture\nsymphony:rework\nrepair-route:luna'

assert_fails $'model:luna\nmodel:sol'
assert_fails $'risk:normal\nrisk:investigative'
assert_fails $'risk:mechanical\nrisk:architecture'
assert_fails $'effort:low\neffort:high'
assert_fails $'symphony:rework\nrepair-route:luna\nrepair-route:sol'
assert_fails 'model:terra'
assert_fails $'symphony:rework\nrepair-route:terra'

# Provider policy is orthogonal to model and effort policy.
assert_provider() {
  local expected="$1"
  local labels="$2"
  local actual
  actual="$(rpgk_select_provider "$labels")"
  if [[ "$actual" != "$expected" ]]; then
    echo "Expected provider '$expected' but got '$actual' for: $labels" >&2
    exit 1
  fi
}

assert_provider_fails() {
  if rpgk_select_provider "$1" >/dev/null 2>&1; then
    echo "Expected invalid provider labels to fail: $1" >&2
    exit 1
  fi
}

assert_provider 
codex\tdefault\tunclassified' ""
assert_provider 
codex\tdefault\tunclassified' "agent:auto"
assert_provider 
codex\tauto\twork:deterministic' 
agent:auto\nwork:deterministic'
assert_provider 
claude\tauto\twork:visual-unity' "work:visual-unity"
assert_provider 
claude\tauto\tvisual-scene-composition' "authoring:scene-existing-composition"
assert_provider 
claude\tauto\tvisual-scene-composition' "authoring:scene-new-composition"
assert_provider 
codex\tdefault\tunclassified' 
resource:unity-editor\nvalidation:unity-required'
assert_provider 
codex\toverride\tagent:codex' 
agent:codex\nwork:visual-unity'
assert_provider 
claude\toverride\tagent:claude' 
agent:claude\nwork:deterministic'
assert_provider_fails 
agent:codex\nagent:claude'
assert_provider_fails 
agent:auto\nagent:claude'
assert_provider_fails 
work:deterministic\nwork:visual-unity'
assert_provider_fails 
agent:codex\nwork:deterministic\nwork:visual-unity'

echo "routing-policy-test: PASS"
