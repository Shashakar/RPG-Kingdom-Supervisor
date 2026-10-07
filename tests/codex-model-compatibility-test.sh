#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/state"
export PATH="$TMP/bin:$PATH"
export RPGK_SUPERVISOR_STATE_ROOT="$TMP/state"

cat > "$TMP/bin/codex" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  echo "codex-cli test"
  exit 0
fi
printf '%s\n' "$*" >> "${RPGK_TEST_CODEX_CALLS:?}"
if [[ "$*" == *"--model bad-sol"* ]]; then
  echo "ERROR: The 'bad-sol' model is not supported when using Codex with a ChatGPT account." >&2
  exit 1
fi
echo "MODEL_OK"
MOCK
chmod +x "$TMP/bin/codex"
export RPGK_TEST_CODEX_CALLS="$TMP/calls"

bash "$ROOT/scripts/codex-model-compatibility.sh" --model good-sol --issue GH-1
[[ ! -e "$TMP/state/model-errors/GH-1.json" ]]
[[ "$(wc -l < "$TMP/calls")" -eq 1 ]]

# Compatible model/version pairs are cached and do not spend another probe turn.
bash "$ROOT/scripts/codex-model-compatibility.sh" --model good-sol --issue GH-2
[[ "$(wc -l < "$TMP/calls")" -eq 1 ]]

set +e
bash "$ROOT/scripts/codex-model-compatibility.sh" --model bad-sol --issue GH-3 >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 78 ]]
jq -e '.classification == "model_unavailable" and .model == "bad-sol" and (.message | contains("not supported"))'   "$TMP/state/model-errors/GH-3.json" >/dev/null

echo "codex-model-compatibility-test: PASS"
