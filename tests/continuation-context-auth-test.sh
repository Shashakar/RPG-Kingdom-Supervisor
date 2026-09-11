#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin" "$TMP/supervisor/scripts"

cat > "$TMP/bin/gh" <<'GH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "auth" && "$2" == "token" ]]; then
  printf '%s\n' 'fixture-host-token'
  exit 0
fi
exit 64
GH
chmod +x "$TMP/bin/gh"

cat > "$TMP/supervisor/scripts/build-continuation-context.sh" <<'BUILDER'
#!/usr/bin/env bash
set -euo pipefail
[[ "${SYMPHONY_GITHUB_TOKEN:-}" == "fixture-host-token" ]]
printf '%s\n' "builder-token-ok"
BUILDER
chmod +x "$TMP/supervisor/scripts/build-continuation-context.sh"

output="$({
  env -u SYMPHONY_GITHUB_TOKEN \
    PATH="$TMP/bin:$PATH" \
    RPGK_SUPERVISOR_ROOT="$TMP/supervisor" \
    bash "$ROOT/scripts/build-continuation-context-with-auth.sh"
} 2>&1)"

grep -Fq 'builder-token-ok' <<<"$output"

# The wrapper must not leak the recovered credential into its caller.
[[ -z "${SYMPHONY_GITHUB_TOKEN:-}" ]]

echo "continuation-context-auth-test: PASS"
