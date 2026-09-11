#!/usr/bin/env bash
set -euo pipefail

REPO="${RPGK_REPO:-Shashakar/RPG-Kingdom}"

create_label() {
  local name="$1"
  local color="$2"
  local description="$3"
  gh label create "$name" \
    --repo "$REPO" \
    --color "$color" \
    --description "$description" \
    --force >/dev/null
  echo "label ready: $name"
}

create_label "symphony:ready" "0E8A16" "Dispatch lease for unattended Symphony implementation"
create_label "symphony:halted" "D93F0B" "Automatic redispatch stopped; human/ChatGPT review required"
create_label "symphony:rearm" "BFDADC" "One-shot approval for a reviewed continuation; consumed by host preflight"
create_label "symphony:agent-review" "1D76DB" "Implementation handoff complete; independent automated review pending"
create_label "symphony:rework" "FBCA04" "Automated review requested bounded repair on the existing PR"
create_label "symphony:human-review" "0E8A16" "Automated review passed; human integration decision required"
create_label "symphony:human-attention" "B60205" "Automation halted for ambiguity, scope change, or exhausted repair budget"

create_label "repair-route:luna" "EDEDED" "Reviewer advisory route for current bounded repair: Luna"
create_label "repair-route:terra" "DDEEFF" "Reviewer advisory route for current bounded repair: Terra"
create_label "repair-route:sol" "FFD966" "Reviewer advisory route for current bounded repair: Sol"
create_label "repair-route:astra" "8B5CF6" "Reviewer advisory route for current bounded repair: Astra"

create_label "risk:mechanical" "C5DEF5" "Mechanical/docs/repetitive work; defaults to Luna / low"
create_label "risk:normal" "BFD4F2" "Normal bounded implementation; defaults to Luna / medium"
create_label "risk:investigative" "1D76DB" "Ambiguous debugging or multi-layer investigation; defaults to Terra / medium"
create_label "risk:architecture" "D4C5F9" "Architecture-sensitive/cross-system work; defaults to Sol / high"
create_label "risk:end-to-end" "5319E7" "Hardest end-to-end execution; defaults to Astra / medium"

create_label "model:luna" "EDEDED" "Explicitly route this Symphony task to GPT-5.6 Luna"
create_label "model:terra" "DDEEFF" "Explicitly route this Symphony task to GPT-5.6 Terra"
create_label "model:sol" "FFD966" "Explicitly route this Symphony task to GPT-5.6 Sol"
create_label "model:astra" "8B5CF6" "Explicitly route this Symphony task to GPT-6 Astra"

create_label "effort:low" "EDEDED" "Explicit Codex reasoning effort: low"
create_label "effort:medium" "FBCA04" "Explicit Codex reasoning effort: medium"
create_label "effort:high" "B60205" "Explicit Codex reasoning effort: high"

create_label "resource:unity-editor" "0B6E99" "Requires exclusive host-owned Unity editor access"
create_label "validation:unity-required" "B60205" "Unity validation must be available before Codex starts"
create_label "validation:unity-optional" "FBCA04" "Work may proceed without Unity; missing validation must be reported"
