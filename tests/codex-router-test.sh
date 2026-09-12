#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/GH-321" "$TMP/state" "$TMP/codex-home"

cat > "$TMP/bin/gh" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "${RPGK_TEST_LABELS:-}"
MOCK
chmod +x "$TMP/bin/gh"

export PATH="$TMP/bin:$PATH"
export RPGK_SUPERVISOR_ROOT="$ROOT"
export RPGK_SUPERVISOR_STATE_ROOT="$TMP/state"
export CODEX_HOME="$TMP/codex-home"
export RPGK_USAGE_SNAPSHOT_TIMEOUT_SECONDS=1
export RPGK_ROUTER_DRY_RUN=1
cd "$TMP/GH-321"

export RPGK_TEST_LABELS="risk:mechanical"
actual="$(bash "$ROOT/scripts/codex-app-server-router.sh")"
[[ "$actual" == $'gpt-5.6-luna\tlow\tluna' ]]

export RPGK_TEST_LABELS="risk:normal"
actual="$(bash "$ROOT/scripts/codex-app-server-router.sh")"
[[ "$actual" == $'gpt-5.6-luna\tmedium\tluna' ]]

export RPGK_TEST_LABELS="risk:investigative"
actual="$(bash "$ROOT/scripts/codex-app-server-router.sh")"
[[ "$actual" == $'gpt-5.6-terra\tmedium\tterra' ]]

export RPGK_TEST_LABELS=$'risk:architecture\nmodel:astra\neffort:high'
actual="$(bash "$ROOT/scripts/codex-app-server-router.sh")"
[[ "$actual" == $'gpt-6-astra\thigh\tastra' ]]

export RPGK_TEST_LABELS=$'model:luna\nmodel:sol'
if bash "$ROOT/scripts/codex-app-server-router.sh" >/dev/null 2>&1; then
  echo "Router accepted conflicting model labels" >&2
  exit 1
fi

# Verify the live App Server launch receives the named permission profile rather
# than relying on Symphony's legacy workspace-write sandbox. Git metadata is now
# intentionally host-owned, and the tracker token must not reach the Codex child.
cat > "$TMP/bin/codex" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
if [[ -n "${SYMPHONY_GITHUB_TOKEN:-}" ]]; then
  echo "tracker token leaked into Codex child" >&2
  exit 91
fi
printf '%s\n' "$@" > "$RPGK_TEST_CODEX_ARGS"
MOCK
chmod +x "$TMP/bin/codex"

export RPGK_ROUTER_DRY_RUN=0
export RPGK_TEST_LABELS="risk:normal"
export RPGK_TEST_CODEX_ARGS="$TMP/codex-args.txt"
export SYMPHONY_GITHUB_TOKEN="test-secret-must-not-reach-codex"
bash "$ROOT/scripts/codex-app-server-router.sh" >/dev/null

grep -Fxq 'default_permissions="rpgk_supervisor_workspace"' "$RPGK_TEST_CODEX_ARGS"
grep -Fq 'permissions.rpgk_supervisor_workspace=' "$RPGK_TEST_CODEX_ARGS"
grep -Fq '":workspace_roots"={"."="write"}' "$RPGK_TEST_CODEX_ARGS"
if grep -Fq '".git"="write"' "$RPGK_TEST_CODEX_ARGS"; then
  echo "Router still grants model-side Git metadata writes" >&2
  exit 1
fi
grep -Fxq 'model="gpt-5.6-luna"' "$RPGK_TEST_CODEX_ARGS"
grep -Fxq 'model_reasoning_effort=medium' "$RPGK_TEST_CODEX_ARGS"
grep -Fxq 'app-server' "$RPGK_TEST_CODEX_ARGS"

python3 - "$TMP/state" <<'PY'
import json, sys
from pathlib import Path
active = Path(sys.argv[1]) / "workers" / "active" / "implementation.json"
value = json.loads(active.read_text(encoding="utf-8"))
assert value["identifier"] == "GH-321"
assert value["role"] == "implementation"
assert value["model"] == "gpt-5.6-luna"
assert value["effort"] == "medium"
PY

echo "codex-router-test: PASS"
