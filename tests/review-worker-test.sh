#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/GH-321" "$TMP/state"

cat > "$TMP/bin/codex" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
echo 'usage_limit_exceeded: You have hit your usage limit. try again at tomorrow.' >&2
exit 1
MOCK
chmod +x "$TMP/bin/codex"

cat > "$TMP/prompt.txt" <<'EOF'
Review this fixture.
EOF

export PATH="$TMP/bin:$PATH"
export RPGK_SUPERVISOR_ROOT="$ROOT"
export RPGK_SUPERVISOR_STATE_ROOT="$TMP/state"
export CODEX_HOME="$TMP/codex-home"
export RPGK_USAGE_SNAPSHOT_TIMEOUT_SECONDS=1

bash "$ROOT/scripts/review-worker.sh" \
  "$TMP/GH-321" \
  "$TMP/prompt.txt" \
  "$TMP/verdict.json" \
  gpt-5.6-terra \
  medium

python3 - "$TMP/verdict.json" "$TMP/state" <<'PY'
import json, sys
from pathlib import Path
value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert value["verdict"] == "blocked_or_ambiguous"
assert value["requires_human"] is True
assert value["routing_recommendation"] == "unchanged"
assert "quota" in value["summary"].lower()
state = Path(sys.argv[2])
history = [json.loads(line) for line in (state / "workers" / "history.jsonl").read_text(encoding="utf-8").splitlines()]
assert history[-1]["role"] == "review"
assert history[-1]["identifier"] == "GH-321"
assert history[-1]["outcome"] == "blocked_or_ambiguous"
assert not (state / "workers" / "active" / "review.json").exists()
PY

grep -Fq 'usage_limit_exceeded' "$TMP/verdict.json.codex.log"

echo "review-worker-test: PASS"
