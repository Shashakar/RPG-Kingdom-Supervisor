#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=../scripts/codex-permission-profile.sh
source "$ROOT/scripts/codex-permission-profile.sh"

[[ "$RPGK_CODEX_PERMISSION_PROFILE" == "rpgk_supervisor_workspace" ]]
[[ "$RPGK_CODEX_PERMISSION_PROFILE_TOML" == *'extends=":workspace"'* ]]
[[ "$RPGK_CODEX_PERMISSION_PROFILE_TOML" == *'".git"="write"'* ]]
[[ "$RPGK_CODEX_PERMISSION_PROFILE_TOML" == *'network={enabled=true}'* ]]

if grep -Eq '^[[:space:]]*(thread_sandbox|turn_sandbox_policy):' "$ROOT/WORKFLOW.md"; then
  echo "WORKFLOW.md still opts into the legacy Codex sandbox model" >&2
  exit 1
fi

grep -q 'codex-permission-profile.sh' "$ROOT/scripts/codex-app-server-router.sh"
grep -q 'RPGK_CODEX_PERMISSION_ARGS' "$ROOT/scripts/codex-app-server-router.sh"
grep -q 'codex-git-write-probe.sh' "$ROOT/scripts/run-symphony.sh"
grep -qE '^[[:space:]]*sandbox[[:space:]]*\\?$' "$ROOT/scripts/codex-git-write-probe.sh"

# Only inspect executable shell lines. The probe intentionally documents the
# obsolete `codex sandbox linux ...` CLI form in comments; comments must not
# make this policy test fail.
probe_code="$(grep -vE '^[[:space:]]*#' "$ROOT/scripts/codex-git-write-probe.sh")"
if grep -qE 'sandbox[[:space:]]+\$?"?sandbox_subcommand|sandbox[[:space:]]+(linux|macos)([[:space:]]|$)' <<<"$probe_code"; then
  echo "Codex Git-write probe uses an obsolete host sandbox subcommand" >&2
  exit 1
fi

bash -n "$ROOT/scripts/codex-permission-profile.sh"
bash -n "$ROOT/scripts/codex-git-write-probe.sh"

echo "codex-permissions-policy-test: PASS"
