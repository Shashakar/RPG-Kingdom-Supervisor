#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW="$ROOT/WORKFLOW.md"
TRANSFORM="$ROOT/scripts/patch-symphony-named-permissions.py"
RUNNER="$ROOT/scripts/run-symphony.sh"
APPLY="$ROOT/scripts/apply-symphony-permissions-patch.sh"
VERIFY="$ROOT/scripts/verify-symphony-permissions-patch.sh"

for file in "$WORKFLOW" "$TRANSFORM" "$RUNNER" "$APPLY" "$VERIFY"; do
  [[ -f "$file" ]] || {
    echo "symphony-permissions-compat-test: missing $file" >&2
    exit 1
  }
done

grep -Fq 'permissions: rpgk_supervisor_workspace' "$WORKFLOW" || {
  echo "symphony-permissions-compat-test: WORKFLOW must select the named Codex permission profile" >&2
  exit 1
}

grep -Fq 'field(:permissions, :string)' "$TRANSFORM" || {
  echo "symphony-permissions-compat-test: transform must add the Symphony codex.permissions schema field" >&2
  exit 1
}

grep -Fq 'Map.put(params, "permissions", permissions)' "$TRANSFORM" || {
  echo "symphony-permissions-compat-test: transform must send named permissions to Codex App Server" >&2
  exit 1
}

runtime_root_count="$(grep -Fc '"runtimeWorkspaceRoots" => [workspace]' "$TRANSFORM" || true)"
if [[ "$runtime_root_count" -lt 2 ]]; then
  echo "symphony-permissions-compat-test: transform must materialize the issue workspace as runtimeWorkspaceRoots for both thread/start and turn/start" >&2
  exit 1
fi

grep -Fq 'Map.put(params, "sandbox", thread_sandbox)' "$TRANSFORM" || {
  echo "symphony-permissions-compat-test: transform must preserve legacy thread-sandbox fallback" >&2
  exit 1
}

grep -Fq 'Map.put(params, "sandboxPolicy", turn_sandbox_policy)' "$TRANSFORM" || {
  echo "symphony-permissions-compat-test: transform must preserve legacy turn-sandbox fallback" >&2
  exit 1
}

grep -Fq 'python3 "$PERMISSIONS_TRANSFORM" "$SYMPHONY_ROOT"' "$APPLY" || {
  echo "symphony-permissions-compat-test: installer must use the deterministic pinned-source permissions transform" >&2
  exit 1
}

grep -Fq 'git -C "$SYMPHONY_REPO_ROOT" reset --hard "$PIN"' "$APPLY" || {
  echo "symphony-permissions-compat-test: installer must be able to safely rebuild the dedicated generated compatibility branch from the evaluated pin" >&2
  exit 1
}

grep -Fq 'current_branch" == "$LOCAL_BRANCH"' "$APPLY" || {
  echo "symphony-permissions-compat-test: installer must restrict generated-branch reset behavior to the dedicated compatibility branch" >&2
  exit 1
}

grep -Fq 'runtimeWorkspaceRoots' "$VERIFY" || {
  echo "symphony-permissions-compat-test: verifier must require runtime workspace root propagation" >&2
  exit 1
}

grep -Fq 'verify-symphony-permissions-patch.sh' "$RUNNER" || {
  echo "symphony-permissions-compat-test: run-symphony must fail closed when the compatibility patch is absent" >&2
  exit 1
}

grep -Fq 'apply-symphony-permissions-patch.sh' "$RUNNER" || {
  echo "symphony-permissions-compat-test: run-symphony must provide the repair command" >&2
  exit 1
}

python3 -m py_compile "$TRANSFORM"
bash -n "$APPLY"
bash -n "$VERIFY"
bash -n "$RUNNER"

echo "symphony-permissions-compat-test: PASS"
