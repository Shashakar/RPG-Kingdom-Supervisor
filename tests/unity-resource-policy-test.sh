#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../scripts/unity-resource-policy.sh
source "$ROOT/scripts/unity-resource-policy.sh"

assert_policy() {
  local labels="$1"
  local expected="$2"
  local actual
  actual="$(rpgk_select_unity_policy "$labels")"
  [[ "$actual" == "$expected" ]] || {
    echo "expected policy '$expected', got '$actual' for labels: $labels" >&2
    exit 1
  }
}

assert_policy "" $'none\tnone'
assert_policy "validation:unity-optional" $'none\toptional'
assert_policy "resource:unity-editor" $'unity-editor\tnone'
assert_policy $'resource:unity-editor\nvalidation:unity-optional' $'unity-editor\toptional'
assert_policy $'resource:unity-editor\nvalidation:unity-required' $'unity-editor\trequired'

if rpgk_select_unity_policy $'validation:unity-required' >/dev/null 2>&1; then
  echo "expected required Unity validation without the resource label to fail" >&2
  exit 1
fi

if rpgk_select_unity_policy $'validation:unity-required\nvalidation:unity-optional\nresource:unity-editor' >/dev/null 2>&1; then
  echo "expected conflicting Unity validation labels to fail" >&2
  exit 1
fi

echo "unity-resource-policy-test: PASS"
