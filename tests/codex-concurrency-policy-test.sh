#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# shellcheck source=../scripts/codex-concurrency-policy.sh
source "$ROOT/scripts/codex-concurrency-policy.sh"

[[ "$(rpgk_codex_slot_for_role implementation)" == mutation ]]
[[ "$(rpgk_codex_slot_for_role repair)" == mutation ]]
[[ "$(rpgk_codex_slot_for_role report-only)" == mutation ]]
[[ "$(rpgk_codex_slot_for_role review)" == review ]]
[[ "$(rpgk_codex_max_total_processes)" == 2 ]]
if rpgk_codex_slot_for_role mystery >/dev/null 2>&1; then
  echo "unsupported role unexpectedly received a Codex slot" >&2
  exit 1
fi
if rpgk_codex_issue_lock_path "$TMP" not-an-issue >/dev/null 2>&1; then
  echo "invalid issue identifier unexpectedly received a lock path" >&2
  exit 1
fi

mutation_lock="$(rpgk_codex_slot_lock_path "$TMP" implementation)"
review_lock="$(rpgk_codex_slot_lock_path "$TMP" review)"
issue_101="$(rpgk_codex_issue_lock_path "$TMP" GH-101)"
issue_102="$(rpgk_codex_issue_lock_path "$TMP" GH-102)"
mkdir -p "$(dirname "$mutation_lock")" "$(dirname "$issue_101")"

# Hold the mutation slot as implementation. Repair/report-only map to the same file and must fail.
exec 91>"$mutation_lock"
flock 91
if flock -n "$mutation_lock" -c true; then
  echo "second mutation worker acquired the single mutation slot" >&2
  exit 1
fi

# Review owns an independent slot and therefore may overlap the live mutation slot.
flock -n "$review_lock" -c true
exec 92>"$review_lock"
flock 92
if flock -n "$review_lock" -c true; then
  echo "second reviewer acquired the single review slot" >&2
  exit 1
fi

# Same-issue ownership blocks review/mutation overlap while an unrelated issue remains available.
exec 93>"$issue_101"
flock 93
if flock -n "$issue_101" -c true; then
  echo "same issue lock was acquired concurrently" >&2
  exit 1
fi
flock -n "$issue_102" -c true

# Source-level integration checks: both launchers must use the common policy and issue ownership.
grep -Fq 'source "$SUPERVISOR_ROOT/scripts/codex-concurrency-policy.sh"' "$ROOT/scripts/codex-app-server-router.sh"
grep -Fq 'source "$SUPERVISOR_ROOT/scripts/codex-concurrency-policy.sh"' "$ROOT/scripts/review-worker.sh"
grep -Fq 'rpgk_codex_slot_lock_path "$STATE_ROOT" "$role"' "$ROOT/scripts/codex-app-server-router.sh"
grep -Fq 'rpgk_codex_slot_lock_path "$STATE_ROOT" review' "$ROOT/scripts/review-worker.sh"
grep -Fq 'flock -n 8' "$ROOT/scripts/review-worker.sh"

if grep -Fq 'codex-session.lock' "$ROOT/scripts/codex-app-server-router.sh" "$ROOT/scripts/review-worker.sh"; then
  echo "legacy global Codex session lock is still used by a worker launcher" >&2
  exit 1
fi

echo "codex-concurrency-policy-test: PASS"
