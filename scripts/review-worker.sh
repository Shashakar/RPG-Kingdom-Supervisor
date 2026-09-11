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
LOCK_FILE="$STATE_ROOT/locks/codex-session.lock"

[[ -f "$prompt_file" ]] || { echo "Review prompt missing: $prompt_file" >&2; exit 64; }
[[ -f "$SCHEMA" ]] || { echo "Review schema missing: $SCHEMA" >&2; exit 64; }
mkdir -p "$(dirname "$output_file")" "$(dirname "$LOCK_FILE")"

# The reviewer is intentionally independent and read-only. It may inspect repository state,
# ignored validation artifacts, and Git history, but it cannot mutate the implementation.
unset SYMPHONY_GITHUB_TOKEN GH_TOKEN GITHUB_TOKEN
exec 9>"$LOCK_FILE"
flock 9

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
  "$(cat "$prompt_file")"

python3 - "$output_file" "$SCHEMA" <<'PY'
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
if value["routing_recommendation"] not in {"luna", "terra", "sol", "astra", "unchanged"}:
    raise SystemExit("invalid routing recommendation")
if not isinstance(value["findings"], list):
    raise SystemExit("review findings must be an array")
print("RPG Kingdom review worker: structured verdict validated")
PY
