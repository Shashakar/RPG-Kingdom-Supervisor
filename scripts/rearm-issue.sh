#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^[0-9]+$ ]]; then
  echo "Usage: $0 <issue-number>" >&2
  exit 64
fi

issue_number="$1"
issue_identifier="GH-$issue_number"
repo="${RPGK_REPO:-Shashakar/RPG-Kingdom}"

labels="$(gh issue view "$issue_number" --repo "$repo" --json labels --jq '.labels[].name')"
if grep -Fxiq 'symphony:halted' <<<"$labels"; then
  gh issue edit "$issue_number" --repo "$repo" --remove-label 'symphony:halted' >/dev/null
fi

# Rearm is intentionally remote-first so the same one-shot contract can be requested by a
# human/ChatGPT review tool that cannot touch Supervisor host state. Add rearm before ready so
# Symphony cannot observe the dispatch lease without the corresponding continuation approval.
gh issue edit "$issue_number" --repo "$repo" --add-label 'symphony:rearm' >/dev/null
if grep -Fxiq 'agent:claude' <<<"$labels"; then
  # Do not ever make a Claude worker eligible for Symphony's Codex-only tracker.
  gh issue edit "$issue_number" --repo "$repo" --remove-label 'symphony:ready' >/dev/null 2>&1 || true
  gh issue edit "$issue_number" --repo "$repo" --add-label 'symphony:claude-ready' >/dev/null
  echo "Requested one Claude continuation for $issue_identifier (symphony:claude-ready)."
else
  gh issue edit "$issue_number" --repo "$repo" --add-label 'symphony:ready' >/dev/null
  echo "Requested one Codex continuation for $issue_identifier (symphony:ready)."
fi
