#!/usr/bin/env bash
set -euo pipefail

RPGK_REPO_OWNER="${RPGK_REPO_OWNER:-Shashakar}"
RPGK_REPO_NAME="${RPGK_REPO_NAME:-RPG-Kingdom}"
API_ROOT="${RPGK_GITHUB_API_ROOT:-https://api.github.com}"
TOKEN="${SYMPHONY_GITHUB_TOKEN:-}"
MARKER="${RPGK_ATTEMPT_MARKER:-.symphony-attempt-complete}"
USAGE_LIMIT_MARKER="${RPGK_USAGE_LIMIT_MARKER:-.symphony-usage-limit.json}"
CONTINUATION_STOP_MARKER="${RPGK_CONTINUATION_STOP_MARKER:-.symphony-continuation-stop.json}"
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
SCRIPT_ROOT="$(dirname "${BASH_SOURCE[0]}")"
REVIEW_STATE_WRITER="${RPGK_REVIEW_STATE_WRITER:-$SCRIPT_ROOT/queue-agent-review.py}"
REPORT_RECONCILER="${RPGK_REPORT_RECONCILER:-$SCRIPT_ROOT/reconcile-report-completion.py}"

workspace_name="$(basename "$PWD")"
if [[ ! "$workspace_name" =~ ^GH-([0-9]+)$ ]]; then
  echo "RPG Kingdom budget guard: workspace '$workspace_name' is not a GH issue workspace; no action" >&2
  exit 0
fi
issue_number="${BASH_REMATCH[1]}"

finish_worker_telemetry() {
  local original_status=$?
  trap - EXIT
  set +e
  local active_dir="$STATE_ROOT/workers/active"
  local has_issue_record=0
  if [[ -d "$active_dir" ]]; then
    while IFS= read -r path; do
      if python3 - "$path" "$issue_number" <<'PY' >/dev/null 2>&1
import json, sys
from pathlib import Path
try:
    value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if value.get("issue") == int(sys.argv[2]) else 1)
PY
      then
        has_issue_record=1
        break
      fi
    done < <(find "$active_dir" -maxdepth 1 -type f -name '*.json' -print 2>/dev/null)
  fi

  if (( has_issue_record == 1 )); then
    env -u SYMPHONY_GITHUB_TOKEN -u GITHUB_TOKEN -u GH_TOKEN -u OPENAI_API_KEY \
      python3 "$SCRIPT_ROOT/codex-usage-snapshot.py" --write --quiet >/dev/null 2>&1 || true
    GH_TOKEN="$TOKEN" python3 "$SCRIPT_ROOT/supervisor_telemetry.py" worker-end \
      --workspace "$PWD" --infer-lifecycle >/dev/null 2>&1 || true
  fi
  exit "$original_status"
}
trap finish_worker_telemetry EXIT

prior_attempt_boundary="none"
if [[ -f "$MARKER" ]]; then
  prior_attempt_boundary="$(python3 - "$MARKER" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).stat().st_mtime_ns)
PY
)"
fi

printf 'completed worker lifetime for GH-%s at %s\n' "$issue_number" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$MARKER"

quota_json=""
if [[ -f "$USAGE_LIMIT_MARKER" ]]; then
  quota_json="$(cat "$USAGE_LIMIT_MARKER")"
fi
continuation_json=""
if [[ -f "$CONTINUATION_STOP_MARKER" ]]; then
  continuation_json="$(cat "$CONTINUATION_STOP_MARKER")"
fi

if [[ -z "$TOKEN" ]]; then
  echo "RPG Kingdom budget guard: SYMPHONY_GITHUB_TOKEN is missing; local redispatch marker is active but tracker cleanup could not run" >&2
  exit 70
fi

api() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  local args=(
    -fsS --retry 3 --retry-all-errors -X "$method"
    -H "Authorization: Bearer $TOKEN"
    -H "Accept: application/vnd.github+json"
    -H "X-GitHub-Api-Version: 2022-11-28"
  )
  if [[ -n "$body" ]]; then
    args+=( -H "Content-Type: application/json" -d "$body" )
  fi
  curl "${args[@]}" "$API_ROOT/repos/$RPGK_REPO_OWNER/$RPGK_REPO_NAME$path"
}

ready_present() {
  local labels_json
  labels_json="$(api GET "/issues/$issue_number/labels?per_page=100")"
  jq -e '.[] | select((.name | ascii_downcase) == "symphony:ready")' >/dev/null <<<"$labels_json"
}

enqueue_agent_review() {
  local branch encoded_branch pulls pr_number pr_head
  branch="$(git branch --show-current 2>/dev/null || true)"
  [[ -n "$branch" ]] || return 0
  encoded_branch="$(jq -rn --arg value "$branch" '$value|@uri')"
  pulls="$(api GET "/pulls?state=open&head=$RPGK_REPO_OWNER:$encoded_branch&base=main&per_page=10")"
  pr_number="$(jq -r '.[0].number // empty' <<<"$pulls")"
  pr_head="$(jq -r '.[0].head.sha // empty' <<<"$pulls")"
  [[ -n "$pr_number" && -n "$pr_head" ]] || return 0

  if [[ "${RPGK_GUARD_DRY_RUN:-0}" == "1" ]]; then
    echo "RPG Kingdom review workflow: would queue GH-$issue_number / PR #$pr_number at $pr_head for independent review" >&2
    return 0
  fi

  python3 "$REVIEW_STATE_WRITER" "$issue_number" "$pr_number" "$pr_head"

  for label in symphony%3Arework repair-route%3Aluna repair-route%3Aterra repair-route%3Asol repair-route%3Aastra; do
    api DELETE "/issues/$issue_number/labels/$label" >/dev/null 2>&1 || true
  done
  api POST "/issues/$issue_number/labels" '{"labels":["symphony:agent-review"]}' >/dev/null
  echo "RPG Kingdom review workflow: queued GH-$issue_number / PR #$pr_number for independent review" >&2
}

if [[ "${RPGK_GUARD_DRY_RUN:-0}" != "1" && -f "$REPORT_RECONCILER" ]]; then
  set +e
  report_reconcile_output="$(python3 "$REPORT_RECONCILER" \
    --issue "$issue_number" \
    --workspace "$PWD" \
    --state-root "$STATE_ROOT" \
    --attempt-boundary "$prior_attempt_boundary" 2>&1)"
  report_reconcile_status=$?
  set -e
  if [[ "$report_reconcile_status" -eq 0 ]]; then
    rm -f -- "$USAGE_LIMIT_MARKER" "$CONTINUATION_STOP_MARKER"
    [[ -z "$report_reconcile_output" ]] || echo "$report_reconcile_output" >&2
    echo "RPG Kingdom report workflow: reconciled GH-$issue_number as symphony:report-complete" >&2
    exit 0
  fi
  if [[ "$report_reconcile_status" -ne 2 ]]; then
    [[ -z "$report_reconcile_output" ]] || echo "$report_reconcile_output" >&2
    echo "RPG Kingdom report workflow: trusted report completion exists but lifecycle reconciliation failed; refusing to convert it into symphony:halted" >&2
    exit "$report_reconcile_status"
  fi
fi

if ! ready_present; then
  rm -f -- "$USAGE_LIMIT_MARKER" "$CONTINUATION_STOP_MARKER"
  enqueue_agent_review
  exit 0
fi

if [[ "${RPGK_GUARD_DRY_RUN:-0}" == "1" ]]; then
  if [[ -n "$quota_json" ]]; then
    echo "RPG Kingdom budget guard: would halt GH-$issue_number because Codex usage quota was exhausted" >&2
  elif [[ -n "$continuation_json" ]]; then
    echo "RPG Kingdom budget guard: would halt GH-$issue_number because the continuation policy declined another automatic turn" >&2
  else
    echo "RPG Kingdom budget guard: would halt GH-$issue_number because symphony:ready remains after the worker attempt" >&2
  fi
  exit 0
fi

for _ in 1 2; do
  sleep 1
  if ! ready_present; then
    rm -f -- "$USAGE_LIMIT_MARKER" "$CONTINUATION_STOP_MARKER"
    enqueue_agent_review
    exit 0
  fi
done

api DELETE "/issues/$issue_number/labels/symphony%3Aready" >/dev/null
api POST "/issues/$issue_number/labels" '{"labels":["symphony:halted"]}' >/dev/null

if [[ -n "$quota_json" ]] && jq -e '.reason == "usage_limit_exceeded"' >/dev/null 2>&1 <<<"$quota_json"; then
  retry_at="$(jq -r '.retry_at // empty' <<<"$quota_json")"
  quota_message="$(jq -r '.message // empty' <<<"$quota_json")"

  comment=$(cat <<EOF
Symphony stopped this worker lifetime because Codex reported \`usage_limit_exceeded\`.

No continuation turns were launched after the quota terminal condition, so this is **not** an \`agent.max_turns\` or implementation-budget exhaustion. The issue has been halted and its dispatch lease removed. A reviewed rearm is appropriate once Codex quota is available again.$([[ -n "$retry_at" ]] && printf '\n\nReported retry/reset time: `%s`.' "$retry_at")$([[ -n "$quota_message" ]] && printf '\n\nCodex message: `%s`' "$quota_message")
EOF
)
  comment_json="$(jq -n --arg body "$comment" '{body:$body}')"
  api POST "/issues/$issue_number/comments" "$comment_json" >/dev/null
  rm -f -- "$USAGE_LIMIT_MARKER"
  echo "RPG Kingdom budget guard: halted GH-$issue_number for Codex usage quota and removed symphony:ready" >&2
  exit 0
fi

if [[ -n "$continuation_json" ]] && jq -e '.decision == "stop"' >/dev/null 2>&1 <<<"$continuation_json"; then
  route="$(jq -r '.route // "unknown"' <<<"$continuation_json")"
  turn="$(jq -r '.turn // "?"' <<<"$continuation_json")"
  hard_max="$(jq -r '.hardMaxTurns // "?"' <<<"$continuation_json")"
  reason="$(jq -r '.reason // "continuation policy declined another turn"' <<<"$continuation_json")"
  primary="$(jq -r '.quotaDecision.primaryRemainingPercent // "unavailable"' <<<"$continuation_json")"
  weekly="$(jq -r '.quotaDecision.weeklyRemainingPercent // "unavailable"' <<<"$continuation_json")"
  total_tokens="$(jq -r '.usage.totalTokens // "unavailable"' <<<"$continuation_json")"
  unity_run="$(jq -r '.unity.runId // "none"' <<<"$continuation_json")"
  unity_result="$(jq -r '.unity.result // "none"' <<<"$continuation_json")"

  comment=$(cat <<EOF
Symphony stopped automatic continuation after turn **$turn/$hard_max** because the Supervisor continuation policy declined another model turn.

- Route: \`$route\`
- Decision: \`continuation-budget-stop\`
- Reason: $reason
- Current authoritative quota at the decision: 5h **$primary%** remaining; weekly **$weekly%** remaining
- Cumulative rollout tokens observed: **$total_tokens**
- Latest Unity evidence: \`$unity_run\` — \`$unity_result\`

Useful workspace changes and validation artifacts were preserved. This is distinct from Codex \`usage_limit_exceeded\` and from blindly reaching \`agent.max_turns\`. Review the preserved work before using the one-shot rearm path.
EOF
)
  comment_json="$(jq -n --arg body "$comment" '{body:$body}')"
  api POST "/issues/$issue_number/comments" "$comment_json" >/dev/null
  echo "RPG Kingdom budget guard: halted GH-$issue_number at continuation-policy boundary and removed symphony:ready" >&2
  exit 0
fi

comment=$(cat <<'EOF'
Symphony's Phase 2 budget guard stopped automatic redispatch because this worker attempt ended while `symphony:ready` was still present.

This usually means the task exhausted the configured turn budget or the worker failed before completing the PR handoff. The workspace is also locally marked as having consumed its worker-lifetime budget, so another Codex App Server session will not start even if the GitHub lease cleanup is delayed. Review the Symphony/Codex logs and current workspace/PR state before retrying. Explicitly rearm the issue only after deciding another bounded run is justified.
EOF
)
comment_json="$(jq -n --arg body "$comment" '{body:$body}')"
api POST "/issues/$issue_number/comments" "$comment_json" >/dev/null
rm -f -- "$USAGE_LIMIT_MARKER"

echo "RPG Kingdom budget guard: halted GH-$issue_number and removed symphony:ready" >&2
