#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/GH-321"

cat > "$TMP/bin/gh" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "${RPGK_TEST_LABELS:-}"
MOCK
chmod +x "$TMP/bin/gh"

export PATH="$TMP/bin:$PATH"
export RPGK_SUPERVISOR_ROOT="$ROOT"
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

echo "codex-router-test: PASS"
