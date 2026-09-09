#!/usr/bin/env bash
set -euo pipefail

SYMPHONY_ROOT="${SYMPHONY_ROOT:-$HOME/src/openai-symphony/elixir}"
SYMPHONY_REPO_ROOT="$(cd "$SYMPHONY_ROOT/.." && pwd)"
PIN="8001b52e3062495a16e520e4ceaf8f9de868c4d0"

schema="$SYMPHONY_ROOT/lib/symphony_elixir/config/schema.ex"
config="$SYMPHONY_ROOT/lib/symphony_elixir/config.ex"
app_server="$SYMPHONY_ROOT/lib/symphony_elixir/codex/app_server.ex"

for file in "$schema" "$config" "$app_server"; do
  if [[ ! -f "$file" ]]; then
    echo "ERROR: expected patched Symphony source is missing: $file" >&2
    exit 1
  fi
done

if ! git -C "$SYMPHONY_REPO_ROOT" merge-base --is-ancestor "$PIN" HEAD 2>/dev/null; then
  echo "ERROR: active Symphony checkout is not based on the evaluated upstream pin $PIN" >&2
  exit 1
fi

if ! grep -Fq 'field(:permissions, :string)' "$schema"; then
  echo "ERROR: Symphony schema does not expose codex.permissions" >&2
  exit 1
fi

if ! grep -Fq 'permissions: settings.codex.permissions' "$config"; then
  echo "ERROR: Symphony runtime settings do not propagate codex.permissions" >&2
  exit 1
fi

if ! grep -Fq 'Map.put(params, "permissions", permissions)' "$app_server"; then
  echo "ERROR: Symphony App Server client does not send the named permission profile" >&2
  exit 1
fi

runtime_root_count="$(grep -Fc '"runtimeWorkspaceRoots" => [workspace]' "$app_server" || true)"
if [[ "$runtime_root_count" -lt 2 ]]; then
  echo "ERROR: Symphony App Server client does not materialize the issue workspace as the runtime workspace root on both thread/start and turn/start" >&2
  exit 1
fi

if ! grep -Fq 'Map.put(params, "sandbox", thread_sandbox)' "$app_server"; then
  echo "ERROR: Symphony App Server client lost legacy thread-sandbox fallback" >&2
  exit 1
fi

if ! grep -Fq 'Map.put(params, "sandboxPolicy", turn_sandbox_policy)' "$app_server"; then
  echo "ERROR: Symphony App Server client lost legacy turn-sandbox fallback" >&2
  exit 1
fi

echo "RPG Kingdom Symphony named-permissions compatibility: PASS"
