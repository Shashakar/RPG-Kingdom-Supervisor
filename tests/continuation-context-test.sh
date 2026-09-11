#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin" "$TMP/GH-98"
cat > "$TMP/bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == "api" ]] || exit 90
shift
if [[ "${1:-}" == "graphql" ]]; then
  cat <<'JSON'
{"data":{"repository":{"pullRequest":{"reviewThreads":{"nodes":[{"isResolved":false,"comments":{"nodes":[{"author":{"login":"reviewer"},"body":"Unresolved inline blocker","createdAt":"2026-09-10T20:30:00Z","url":"https://example.test/thread"}]}},{"isResolved":true,"comments":{"nodes":[{"author":{"login":"reviewer"},"body":"Resolved old note","createdAt":"2026-09-10T19:00:00Z","url":"https://example.test/resolved"}]}}]}}}}}
JSON
  exit 0
fi
case "$1" in
  repos/Shashakar/RPG-Kingdom/issues/98/comments?per_page=100)
    cat <<'JSON'
[{"created_at":"2026-09-10T21:00:00Z","user":{"login":"old"},"body":"Old issue chatter"},{"created_at":"2026-09-10T22:30:00Z","user":{"login":"owner"},"body":"New continuation requirement"}]
JSON
    ;;
  repos/Shashakar/RPG-Kingdom/pulls?state=open\&head=Shashakar:codex%2Fgh-98-humanoid-animator-fix\&per_page=10)
    cat <<'JSON'
[{"number":100,"title":"Fix animator","html_url":"https://example.test/pr/100","head":{"sha":"deadbeef"},"body":"Current PR body"}]
JSON
    ;;
  repos/Shashakar/RPG-Kingdom/pulls/100/reviews?per_page=100)
    cat <<'JSON'
[{"state":"COMMENTED","user":{"login":"reviewer"},"body":"Persistent review blocker from before the last attempt"}]
JSON
    ;;
  repos/Shashakar/RPG-Kingdom/issues/100/comments?per_page=100)
    cat <<'JSON'
[{"created_at":"2026-09-10T21:30:00Z","user":{"login":"old"},"body":"Old PR comment"},{"created_at":"2026-09-10T22:40:00Z","user":{"login":"owner"},"body":"New PR continuation comment"}]
JSON
    ;;
  *)
    echo "unexpected gh api path: $1" >&2
    exit 91
    ;;
esac
EOF
chmod +x "$TMP/bin/gh"

cd "$TMP/GH-98"
git init -q
git config user.email test@example.com
git config user.name test
printf '# RPG Kingdom instructions\nPreserve system boundaries.\n' > AGENTS.md
git add AGENTS.md
git commit -qm init
git checkout -qb codex/gh-98-humanoid-animator-fix
printf 'completed worker lifetime for GH-98 at 2026-09-10T22:00:00Z\n' > .symphony-attempt-complete

PATH="$TMP/bin:$PATH" \
SYMPHONY_GITHUB_TOKEN=fake-token \
RPGK_REPO=Shashakar/RPG-Kingdom \
bash "$ROOT/scripts/build-continuation-context.sh"

[[ -f AGENTS.override.md ]]
grep -Fq 'Preserve system boundaries.' AGENTS.override.md
grep -Fq 'PR: #100 — Fix animator' AGENTS.override.md
grep -Fq 'Current head: `deadbeef`' AGENTS.override.md
grep -Fq 'Persistent review blocker from before the last attempt' AGENTS.override.md
grep -Fq 'Unresolved inline blocker' AGENTS.override.md
grep -Fq 'New continuation requirement' AGENTS.override.md
grep -Fq 'New PR continuation comment' AGENTS.override.md
! grep -Fq 'Old issue chatter' AGENTS.override.md
! grep -Fq 'Old PR comment' AGENTS.override.md
! grep -Fq 'Resolved old note' AGENTS.override.md
git check-ignore -q AGENTS.override.md
[[ -z "$(git status --porcelain -- AGENTS.override.md)" ]]

# A fresh workspace must not retain host-generated continuation instructions.
rm -f .symphony-attempt-complete
PATH="$TMP/bin:$PATH" SYMPHONY_GITHUB_TOKEN=fake-token bash "$ROOT/scripts/build-continuation-context.sh"
[[ ! -e AGENTS.override.md ]]

echo "continuation-context-test: PASS"
