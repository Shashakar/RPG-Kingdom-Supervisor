#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
MODEL=""
ISSUE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="${2:-}"; shift 2 ;;
    --issue) ISSUE="${2:-}"; shift 2 ;;
    *) echo "usage: codex-model-compatibility.sh --model <model> --issue GH-N" >&2; exit 64 ;;
  esac
done

[[ -n "$MODEL" && "$ISSUE" =~ ^GH-[0-9]+$ ]] || {
  echo "RPG Kingdom model compatibility: --model and --issue GH-N are required" >&2
  exit 64
}

mkdir -p "$STATE_ROOT/model-compatibility" "$STATE_ROOT/model-errors"
error_file="$STATE_ROOT/model-errors/$ISSUE.json"
codex_version="$(codex --version 2>&1 | head -n 1 || true)"
[[ -n "$codex_version" ]] || codex_version="unknown"
cache_key="$(printf '%s\n%s\n' "$codex_version" "$MODEL" | sha256sum | awk '{print $1}')"
cache_file="$STATE_ROOT/model-compatibility/$cache_key.json"

if [[ -f "$cache_file" ]] && jq -e --arg model "$MODEL" --arg version "$codex_version"   '.status == "compatible" and .model == $model and .codexVersion == $version' "$cache_file" >/dev/null 2>&1; then
  rm -f -- "$error_file"
  exit 0
fi

probe_dir="$(mktemp -d)"
probe_out="$(mktemp)"
trap 'rm -rf -- "$probe_dir" "$probe_out"' EXIT

set +e
(
  cd "$probe_dir"
  timeout "${RPGK_MODEL_COMPATIBILITY_TIMEOUT_SECONDS:-60}s"     codex exec --skip-git-repo-check --model "$MODEL" --sandbox read-only "Reply with exactly: MODEL_OK"
) >"$probe_out" 2>&1
status=$?
set -e

if [[ "$status" -eq 0 ]] && grep -Fxq "MODEL_OK" "$probe_out"; then
  jq -n     --arg model "$MODEL"     --arg version "$codex_version"     --arg observedAt "$(date -u +%Y-%m-%dT%H:%M:%SZ)"     '{protocolVersion:1,status:"compatible",model:$model,codexVersion:$version,observedAt:$observedAt}'     > "$cache_file"
  rm -f -- "$error_file"
  exit 0
fi

message="$(grep -E "not supported|invalid_request_error|model.*(unavailable|unsupported|not found)|ERROR:" "$probe_out" | tail -n 3 | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g' || true)"
if [[ -z "$message" ]]; then
  message="$(tail -n 8 "$probe_out" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g')"
fi
[[ -n "$message" ]] || message="Codex model compatibility probe failed with exit $status"

jq -n   --arg issue "$ISSUE"   --arg model "$MODEL"   --arg version "$codex_version"   --arg message "$message"   --arg observedAt "$(date -u +%Y-%m-%dT%H:%M:%SZ)"   --argjson exitCode "$status"   '{protocolVersion:1,classification:"model_unavailable",issue:$issue,model:$model,codexVersion:$version,message:$message,exitCode:$exitCode,observedAt:$observedAt}'   > "$error_file"

echo "RPG Kingdom model compatibility: $MODEL is unavailable for the configured Codex account/provider: $message" >&2
exit 78
