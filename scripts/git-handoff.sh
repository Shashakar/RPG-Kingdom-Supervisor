#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_ROOT="${RPGK_SYMPHONY_WORKSPACE_ROOT:-$HOME/code/rpg-kingdom-symphony-workspaces}"
ACK_TIMEOUT_SECONDS="${RPGK_GIT_BROKER_ACK_TIMEOUT_SECONDS:-10}"
RUN_TIMEOUT_SECONDS="${RPGK_GIT_BROKER_TIMEOUT_SECONDS:-600}"

usage() {
  cat >&2 <<'EOF'
Usage:
  git-handoff.sh health [--project PATH]
  git-handoff.sh prepare --branch codex/NAME [--project PATH]
  git-handoff.sh handoff --branch codex/NAME --commit-message MESSAGE --pr-title TITLE [--pr-body BODY | --pr-body-file PATH] [--validation-run RUN_ID ...] [--project PATH]

Environment overrides:
  RPGK_SYMPHONY_WORKSPACE_ROOT
  RPGK_GIT_BROKER_ACK_TIMEOUT_SECONDS
  RPGK_GIT_BROKER_TIMEOUT_SECONDS
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 64
fi

operation="$1"
shift
project="$PWD"
branch=""
commit_message=""
pr_title=""
pr_body=""
pr_body_file=""
validation_runs=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      [[ $# -ge 2 ]] || { echo "Missing value for --project" >&2; exit 64; }
      project="$2"
      shift 2
      ;;
    --branch)
      [[ $# -ge 2 ]] || { echo "Missing value for --branch" >&2; exit 64; }
      branch="$2"
      shift 2
      ;;
    --commit-message)
      [[ $# -ge 2 ]] || { echo "Missing value for --commit-message" >&2; exit 64; }
      commit_message="$2"
      shift 2
      ;;
    --pr-title)
      [[ $# -ge 2 ]] || { echo "Missing value for --pr-title" >&2; exit 64; }
      pr_title="$2"
      shift 2
      ;;
    --pr-body)
      [[ $# -ge 2 ]] || { echo "Missing value for --pr-body" >&2; exit 64; }
      pr_body="$2"
      shift 2
      ;;
    --pr-body-file)
      [[ $# -ge 2 ]] || { echo "Missing value for --pr-body-file" >&2; exit 64; }
      pr_body_file="$2"
      shift 2
      ;;
    --validation-run)
      [[ $# -ge 2 ]] || { echo "Missing value for --validation-run" >&2; exit 64; }
      validation_runs+=("$2")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown Git handoff argument: $1" >&2
      usage
      exit 64
      ;;
  esac
done

case "$operation" in
  health) ;;
  prepare)
    [[ -n "$branch" ]] || { echo "git-handoff.sh prepare requires --branch" >&2; exit 64; }
    ;;
  handoff)
    [[ -n "$branch" ]] || { echo "git-handoff.sh handoff requires --branch" >&2; exit 64; }
    [[ -n "$commit_message" ]] || { echo "git-handoff.sh handoff requires --commit-message" >&2; exit 64; }
    [[ -n "$pr_title" ]] || { echo "git-handoff.sh handoff requires --pr-title" >&2; exit 64; }
    if [[ -n "$pr_body" && -n "$pr_body_file" ]]; then
      echo "Use only one of --pr-body or --pr-body-file" >&2
      exit 64
    fi
    ;;
  *)
    echo "Unknown Git handoff operation: $operation" >&2
    usage
    exit 64
    ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  echo "RPG Kingdom Git handoff: jq is required for broker requests" >&2
  exit 80
fi

project="$(cd "$project" && pwd)"
workspace_root="$(cd "$WORKSPACE_ROOT" && pwd)"
workspace_name="$(basename "$project")"

if [[ ! "$workspace_name" =~ ^GH-([0-9]+)$ || "$(dirname "$project")" != "$workspace_root" ]]; then
  echo "RPG Kingdom Git handoff: project '$project' is not a Symphony GH issue workspace under '$workspace_root'" >&2
  exit 81
fi
issue_number="${BASH_REMATCH[1]}"

if [[ -n "$pr_body_file" ]]; then
  [[ -f "$pr_body_file" ]] || { echo "PR body file does not exist: $pr_body_file" >&2; exit 64; }
  pr_body="$(cat "$pr_body_file")"
fi

broker_dir="$project/Logs/SymphonyGit/.broker"
request_dir="$broker_dir/requests"
ack_dir="$broker_dir/acks"
response_dir="$broker_dir/responses"
mkdir -p "$request_dir" "$ack_dir" "$response_dir"

request_id="${workspace_name}-${operation}-$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM"
request_path="$request_dir/$request_id.json"
ack_path="$ack_dir/$request_id.json"
response_path="$response_dir/$request_id.json"
temp_request="$request_path.tmp.$$"

validation_json='[]'
if (( ${#validation_runs[@]} > 0 )); then
  validation_json="$(printf '%s\n' "${validation_runs[@]}" | jq -R . | jq -s .)"
fi

jq -cn \
  --arg requestId "$request_id" \
  --arg operation "$operation" \
  --argjson issueNumber "$issue_number" \
  --arg branch "$branch" \
  --arg commitMessage "$commit_message" \
  --arg prTitle "$pr_title" \
  --arg prBody "$pr_body" \
  --argjson validationRunIds "$validation_json" \
  '{protocolVersion:1,requestId:$requestId,operation:$operation,issueNumber:$issueNumber,branch:$branch,commitMessage:$commitMessage,prTitle:$prTitle,prBody:$prBody,validationRunIds:$validationRunIds}' \
  > "$temp_request"
mv "$temp_request" "$request_path"

ack_deadline=$((SECONDS + ACK_TIMEOUT_SECONDS))
while [[ ! -f "$ack_path" && ! -f "$response_path" ]]; do
  if (( SECONDS >= ack_deadline )); then
    rm -f "$request_path" 2>/dev/null || true
    echo "RPG Kingdom Git handoff: host broker did not acknowledge the request within ${ACK_TIMEOUT_SECONDS}s; start Symphony through scripts/run-symphony.sh" >&2
    exit 84
  fi
  sleep 0.2
done

run_deadline=$((SECONDS + RUN_TIMEOUT_SECONDS))
while [[ ! -f "$response_path" ]]; do
  if (( SECONDS >= run_deadline )); then
    echo "RPG Kingdom Git handoff: host broker request '$request_id' exceeded ${RUN_TIMEOUT_SECONDS}s" >&2
    exit 85
  fi
  sleep 0.5
done

stdout="$(jq -r '.stdout // ""' "$response_path")"
stderr="$(jq -r '.stderr // ""' "$response_path")"
status="$(jq -r '.status // "failed"' "$response_path")"
exit_code="$(jq -r '.exitCode // 86' "$response_path")"
result="$(jq -c '.result // empty' "$response_path")"

if [[ -n "$stderr" ]]; then
  printf '%s\n' "$stderr" >&2
fi
if [[ -n "$result" ]]; then
  printf '%s\n' "$result"
elif [[ -n "$stdout" ]]; then
  printf '%s\n' "$stdout"
fi

case "$status" in
  HostBusy|TimedOut|StaleRequest|BrokerStopped|InvalidWorkspace|InvalidBranch|InvalidOrigin|UnexpectedBranch|ValidationEvidenceMissing|ValidationEvidenceInvalid|ValidationEvidenceFailed|GitFailed|GitHubApiFailed|GitHubNetworkFailed|GitHubAuthMissing|MainNotIntegrated|NonFastForward|ForbiddenPath|DirtyAfterCommit|NoChanges|PushVerificationFailed)
    echo "RPG Kingdom Git handoff: $status" >&2
    ;;
esac

if [[ ! "$exit_code" =~ ^[0-9]+$ ]]; then
  echo "RPG Kingdom Git handoff: broker returned an invalid exit code" >&2
  exit 86
fi

exit "$exit_code"
