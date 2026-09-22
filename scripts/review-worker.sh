#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "Usage: $0 <workspace> <prompt-file> <output-file> <model> <effort>" >&2
  exit 64
fi

workspace="$(cd "$1" && pwd)"
prompt_file="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
output_file="$3"
model="$4"
effort="$5"
SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
SCHEMA="$SUPERVISOR_ROOT/schemas/review-verdict.schema.json"
RUN_LOG="${output_file}.codex.log"
TELEMETRY_STARTED=0

# shellcheck source=codex-concurrency-policy.sh
source "$SUPERVISOR_ROOT/scripts/codex-concurrency-policy.sh"

workspace_name="$(basename "$workspace")"
if [[ ! "$workspace_name" =~ ^GH-([0-9]+)$ ]]; then
  echo "RPG Kingdom review worker: expected workspace basename GH-<issue>, got '$workspace_name'" >&2
  exit 64
fi
identifier="$workspace_name"
slot_lock="$(rpgk_codex_slot_lock_path "$STATE_ROOT" review)"
issue_lock="$(rpgk_codex_issue_lock_path "$STATE_ROOT" "$identifier")"

[[ -f "$prompt_file" ]] || { echo "Review prompt missing: $prompt_file" >&2; exit 64; }
[[ -f "$SCHEMA" ]] || { echo "Review schema missing: $SCHEMA" >&2; exit 64; }
mkdir -p "$(dirname "$output_file")" "$(dirname "$slot_lock")" "$(dirname "$issue_lock")"

finish_telemetry() {
  if (( TELEMETRY_STARTED == 0 )); then
    return 0
  fi
  python3 "$SUPERVISOR_ROOT/scripts/codex-usage-snapshot.py" --write --quiet || true
  python3 "$SUPERVISOR_ROOT/scripts/supervisor_telemetry.py" worker-end \
    --role review \
    --workspace "$workspace" \
    --outcome-file "$output_file" >/dev/null 2>&1 || true
  TELEMETRY_STARTED=0
}
trap finish_telemetry EXIT

# Review has its own single Codex slot and may overlap an unrelated mutation worker. The issue lock
# is deliberately nonblocking: if the same issue/workspace is still being mutated, defer this review
# instead of reading a moving branch or monopolizing the review slot while waiting.
unset SYMPHONY_GITHUB_TOKEN GH_TOKEN GITHUB_TOKEN
exec 9>"$slot_lock"
flock 9
exec 8>"$issue_lock"
if ! flock -n 8; then
  echo "RPG Kingdom review worker: deferred $identifier because the issue workspace is owned by an active mutation/review lifetime" >&2
  exit 75
fi

python3 "$SUPERVISOR_ROOT/scripts/codex-usage-snapshot.py" --write --quiet || true
if ! python3 "$SUPERVISOR_ROOT/scripts/supervisor_telemetry.py" worker-start \
  --role review \
  --workspace "$workspace" \
  --model "$model" \
  --effort "$effort" \
  --route review \
  --pid "$$" >/dev/null; then
  echo "RPG Kingdom review worker: durable worker telemetry record could not be established" >&2
  exit 70
fi
TELEMETRY_STARTED=1

set +e
codex exec \
  -C "$workspace" \
  --skip-git-repo-check \
  --sandbox read-only \
  --output-schema "$SCHEMA" \
  --output-last-message "$output_file" \
  --config 'approval_policy="never"' \
  --config 'web_search="disabled"' \
  --config "model=\"$model\"" \
  --config "model_reasoning_effort=$effort" \
  "$(cat "$prompt_file")" >"$RUN_LOG" 2>&1
status=$?
set -e

if (( status != 0 )); then
  summary="Independent reviewer execution failed; human attention is required before retrying automated review."
  if grep -Eqi 'usage_limit_exceeded|usage limit|try again at' "$RUN_LOG"; then
    summary="Independent reviewer could not run because Codex usage quota is unavailable; human attention is required before requeueing review after quota resets."
  fi
  python3 - "$output_file" "$summary" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "verdict": "blocked_or_ambiguous",
    "summary": sys.argv[2],
    "findings": [],
    "routing_recommendation": "unchanged",
    "requires_human": True,
    "reason": "other"
}, separators=(",", ":")), encoding="utf-8")
PY
fi

python3 - "$output_file" <<'PY'
import json, sys
from pathlib import Path
output = Path(sys.argv[1])
value = json.loads(output.read_text(encoding="utf-8"))
required = {"verdict", "summary", "findings", "routing_recommendation", "requires_human", "reason"}
missing = required - set(value)
if missing:
    raise SystemExit(f"review output missing fields: {sorted(missing)}")
if value["verdict"] not in {"approved", "changes_required", "blocked_or_ambiguous"}:
    raise SystemExit("invalid review verdict")
if value["routing_recommendation"] not in {"luna", "sol", "astra", "unchanged"}:
    raise SystemExit("invalid routing recommendation")
if not isinstance(value["findings"], list):
    raise SystemExit("review findings must be an array")
print("RPG Kingdom review worker: structured verdict validated")
PY
