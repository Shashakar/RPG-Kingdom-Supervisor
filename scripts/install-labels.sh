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
