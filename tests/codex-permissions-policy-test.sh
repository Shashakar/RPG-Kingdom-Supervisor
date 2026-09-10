#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=../scripts/codex-permission-profile.sh
source "$ROOT/scripts/codex-permission-profile.sh"

[[ "$RPGK_CODEX_PERMISSION_PROFILE" == "rpgk_supervisor_workspace" ]]
[[ "$RPGK_CODEX_PERMISSION_PROFILE_TOML" != *'extends='* ]]
[[ "$RPGK_CODEX_PERMISSION_PROFILE_TOML" == *'":root"="read"'* ]]
[[ "$RPGK_CODEX_PERMISSION_PROFILE_TOML" == *'":workspace_roots"={"."="write"}'* ]]
[[ "$RPGK_CODEX_PERMISSION_PROFILE_TOML" != *'".git"="write"'* ]]
[[ "$RPGK_CODEX_PERMISSION_PROFILE_TOML" == *'network={enabled=true}'* ]]

if grep -Eq '^[[:space:]]*(thread_sandbox|turn_sandbox_policy):' "$ROOT/WORKFLOW.md"; then
  echo "WORKFLOW.md still opts into the legacy Codex sandbox model" >&2
  exit 1
fi

grep -q 'codex-permission-profile.sh' "$ROOT/scripts/codex-app-server-router.sh"
grep -q 'RPGK_CODEX_PERMISSION_ARGS' "$ROOT/scripts/codex-app-server-router.sh"
grep -q 'codex-app-server-permission-probe.sh' "$ROOT/scripts/run-symphony.sh"
if grep -q 'codex-git-write-probe.sh' "$ROOT/scripts/run-symphony.sh"; then
  echo "run-symphony.sh must not require model-side Git metadata writes" >&2
  exit 1
fi

grep -qE '^[[:space:]]*sandbox[[:space:]]*\\?$' "$ROOT/scripts/codex-git-write-probe.sh"

# The legacy Git-write probe remains available only for explicit diagnostics.
# It must keep using the current Codex sandbox CLI shape if an operator runs it.
probe_code="$(grep -vE '^[[:space:]]*#' "$ROOT/scripts/codex-git-write-probe.sh")"
if grep -qE 'sandbox[[:space:]]+\$?"?sandbox_subcommand|sandbox[[:space:]]+(linux|macos)([[:space:]]|$)' <<<"$probe_code"; then
  echo "Codex Git-write probe uses an obsolete host sandbox subcommand" >&2
  exit 1
fi

# The App Server preflight remains model-free and now proves exactly the worker
# capability still required after Git handoff moved to the host: source writes.
grep -q '"method": "thread/start"' "$ROOT/scripts/codex-app-server-permission-probe.py"
grep -q '"ephemeral": True' "$ROOT/scripts/codex-app-server-permission-probe.py"
grep -q '"method": "command/exec"' "$ROOT/scripts/codex-app-server-permission-probe.py"
grep -q '"permissionProfile": profile' "$ROOT/scripts/codex-app-server-permission-probe.py"
grep -q 'rpgk-source-write-probe.txt' "$ROOT/scripts/codex-app-server-permission-probe.py"
grep -q 'app-server-workspace-write-ok' "$ROOT/scripts/codex-app-server-permission-probe.py"
if grep -q '"method": "turn/start"' "$ROOT/scripts/codex-app-server-permission-probe.py"; then
  echo "Codex App Server permission probe must not start a model turn" >&2
  exit 1
fi
if grep -q '.git/FETCH_HEAD' "$ROOT/scripts/codex-app-server-permission-probe.py"; then
  echo "App Server startup probe must not depend on direct .git writes" >&2
  exit 1
fi

grep -q 'activePermissionProfile' "$ROOT/scripts/codex-app-server-permission-probe.py"
grep -q 'experimentalApi' "$ROOT/scripts/codex-app-server-permission-probe.py"
grep -q 'workspace_write=ok' "$ROOT/scripts/codex-app-server-permission-probe.py"

grep -q 'git-handoff.sh" prepare' "$ROOT/WORKFLOW.md"
grep -q 'git-handoff.sh" handoff' "$ROOT/WORKFLOW.md"
grep -q 'Git handoff broker' "$ROOT/scripts/run-symphony.sh"

bash -n "$ROOT/scripts/codex-permission-profile.sh"
bash -n "$ROOT/scripts/codex-git-write-probe.sh"
bash -n "$ROOT/scripts/codex-app-server-permission-probe.sh"
python3 -c 'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))' "$ROOT/scripts/codex-app-server-permission-probe.py"

echo "codex-permissions-policy-test: PASS"
