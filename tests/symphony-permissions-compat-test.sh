#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW="$ROOT/WORKFLOW.md"
PATCH="$ROOT/patches/symphony-named-permissions.patch"
RUNNER="$ROOT/scripts/run-symphony.sh"
APPLY="$ROOT/scripts/apply-symphony-permissions-patch.sh"
VERIFY="$ROOT/scripts/verify-symphony-permissions-patch.sh"

for file in "$WORKFLOW" "$PATCH" "$RUNNER" "$APPLY" "$VERIFY"; do
  [[ -f "$file" ]] || {
    echo "symphony-permissions-compat-test: missing $file" >&2
    exit 1
  }
done

grep -Fq 'permissions: rpgk_supervisor_workspace' "$WORKFLOW" || {
  echo "symphony-permissions-compat-test: WORKFLOW must select the named Codex permission profile" >&2
  exit 1
}

grep -Fq 'field(:permissions, :string)' "$PATCH" || {
  echo "symphony-permissions-compat-test: patch must add the Symphony codex.permissions schema field" >&2
  exit 1
}

grep -Fq 'Map.put(params, "permissions", permissions)' "$PATCH" || {
  echo "symphony-permissions-compat-test: patch must send named permissions to Codex App Server" >&2
  exit 1
}

grep -Fq 'Map.put(params, "sandbox", thread_sandbox)' "$PATCH" || {
  echo "symphony-permissions-compat-test: patch must preserve legacy thread sandbox fallback" >&2
  exit 1
}

grep -Fq 'Map.put(params, "sandboxPolicy", turn_sandbox_policy)' "$PATCH" || {
  echo "symphony-permissions-compat-test: patch must preserve legacy turn sandbox fallback" >&2
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

bash -n "$APPLY"
bash -n "$VERIFY"
bash -n "$RUNNER"

echo "symphony-permissions-compat-test: PASS"
