#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin" "$TMP/GH-142"
cat > "$TMP/bin/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
url="${*: -1}"
case "$url" in
  */issues/142/labels?per_page=100)
    printf '%s\n' "${FAKE_LABELS_JSON:-[]}"
    ;;
  */issues/142)
    printf '%s\n' "${FAKE_ISSUE_JSON:?}"
    ;;
  */issues/142/labels|*/issues/142/comments|*/issues/142/labels/*)
    printf '{}\n'
    ;;
  *)
    printf '{}\n'
    ;;
esac
EOF
chmod +x "$TMP/bin/curl"

cat > "$TMP/fake-unity-runner.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'called\n' >> "$FAKE_RUNNER_CALLS"
[[ "${1:-}" == "health" ]] || { echo "unexpected fake runner command" >&2; exit 99; }
printf '{"status":"ready","unityVersion":"6000.3.10f1"}\n'
EOF
chmod +x "$TMP/fake-unity-runner.sh"

cat > "$TMP/contract.json" <<'EOF'
{
  "schemaVersion": 1,
  "supportedProtocolVersions": [1],
  "supportedTiers": ["mechanical", "mechanical-structural", "new-scene-composition"],
  "operationKindsByTier": [
    {
      "tier": "mechanical-structural",
      "operationKinds": ["add-component", "set-object-reference"]
    },
    {
      "tier": "new-scene-composition",
      "operationKinds": ["set-transform", "reparent-object", "delete-object", "instantiate-existing-prefab"]
    }
  ],
  "structuralComponentAdditions": ["RPGKingdom.Runtime.Character.CharacterState"],
  "componentRemovals": [],
  "newSceneComposition": {"creationOperationKind":"copy-scene"}
}
EOF

export PATH="$TMP/bin:$PATH"
export SYMPHONY_GITHUB_TOKEN="test-token"
export RPGK_SUPERVISOR_STATE_ROOT="$TMP/state"
export RPGK_UNITY_RUNNER="$TMP/fake-unity-runner.sh"
export RPGK_SCENE_AUTHORING_CAPABILITY_CONTRACT="$TMP/contract.json"
export FAKE_RUNNER_CALLS="$TMP/runner-calls"
export FAKE_LABELS_JSON='[{"name":"resource:unity-editor"},{"name":"validation:unity-required"},{"name":"authoring:scene-structural"}]'

reset_state() {
  rm -rf "$TMP/state"
  rm -f "$FAKE_RUNNER_CALLS"
}

run_guard() {
  (cd "$TMP/GH-142" && bash "$ROOT/scripts/unity-resource-guard.sh")
}

# Regression for RPG Kingdom #142: known unsupported component halts before Unity/Codex work.
reset_state
export RPGK_UNITY_GUARD_DRY_RUN=1
export FAKE_ISSUE_JSON='{"state":"open","body":"<!-- symphony-scene-authoring-requirements\n{\"mode\":\"known\",\"tier\":\"mechanical-structural\",\"operations\":[\"add-component\"],\"componentAdditions\":[\"RPGKingdom.Runtime.Progression.ObjectiveAbilityGrantAdapter\"],\"dependency\":\"merge source and extend allowlist\"}\n-->"}'
set +e
run_guard >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 78 ]] || { echo "expected unsupported structural capability to exit 78, got $status" >&2; exit 1; }
[[ ! -e "$FAKE_RUNNER_CALLS" ]] || { echo "known unsupported capability must halt before Unity health/model launch" >&2; exit 1; }
[[ ! -f "$TMP/state/authoring/GH-142.json" ]] || { echo "unsupported capability must not receive an authoring receipt" >&2; exit 1; }
jq -e '.status == "unsupported" and .unsupported[0].kind == "component-addition"' "$TMP/state/structural-authoring-preflight/GH-142.json" >/dev/null

# Fully supported declared requirements preserve the normal Tier-2 path.
reset_state
export RPGK_UNITY_GUARD_DRY_RUN=0
export FAKE_ISSUE_JSON='{"state":"open","body":"<!-- symphony-scene-authoring-requirements\n{\"mode\":\"known\",\"tier\":\"mechanical-structural\",\"operations\":[\"add-component\"],\"componentAdditions\":[\"RPGKingdom.Runtime.Character.CharacterState\"]}\n-->"}'
run_guard >/dev/null
[[ -e "$FAKE_RUNNER_CALLS" ]] || { echo "supported capability should continue into Unity health preflight" >&2; exit 1; }
jq -e '.tier == "mechanical-structural"' "$TMP/state/authoring/GH-142.json" >/dev/null
jq -e '.status == "supported" and .authoringAuthorized == true' "$TMP/state/structural-authoring-preflight/GH-142.json" >/dev/null

# Deferred source-only work can validate source but receives no structural authoring authority.
reset_state
export FAKE_ISSUE_JSON='{"state":"open","body":"<!-- symphony-scene-authoring-requirements\n{\"mode\":\"deferred\",\"tier\":\"mechanical-structural\",\"sourcePhaseOnly\":true,\"operations\":[\"add-component\"],\"dependency\":\"merge concrete types then extend allowlist\"}\n-->"}'
run_guard >/dev/null
[[ -e "$FAKE_RUNNER_CALLS" ]] || { echo "deferred source phase should still reach Unity health preflight" >&2; exit 1; }
[[ ! -f "$TMP/state/authoring/GH-142.json" ]] || { echo "deferred source phase must not receive a structural authoring receipt" >&2; exit 1; }
jq -e '.status == "deferred" and .authoringAuthorized == false' "$TMP/state/structural-authoring-preflight/GH-142.json" >/dev/null

# Declared structural requirements fail closed if the project capability contract is unavailable.
reset_state
export RPGK_SCENE_AUTHORING_CAPABILITY_CONTRACT="$TMP/missing-contract.json"
export FAKE_ISSUE_JSON='{"state":"open","body":"<!-- symphony-scene-authoring-requirements\n{\"mode\":\"known\",\"tier\":\"mechanical-structural\",\"operations\":[\"add-component\"]}\n-->"}'
set +e
run_guard >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 78 ]] || { echo "expected missing capability contract to exit 78, got $status" >&2; exit 1; }
[[ ! -e "$FAKE_RUNNER_CALLS" ]] || { echo "missing capability contract must stop before Unity health/model launch" >&2; exit 1; }
jq -e '.status == "contract_unavailable"' "$TMP/state/structural-authoring-preflight/GH-142.json" >/dev/null


# New-scene composition requires explicit supported requirements and records exact third-tier authority.
reset_state
export RPGK_SCENE_AUTHORING_CAPABILITY_CONTRACT="$TMP/contract.json"
export FAKE_LABELS_JSON='[{"name":"resource:unity-editor"},{"name":"validation:unity-required"},{"name":"authoring:scene-new-composition"}]'
export FAKE_ISSUE_JSON='{"state":"open","body":"<!-- symphony-scene-authoring-requirements\n{\"mode\":\"known\",\"tier\":\"new-scene-composition\",\"operations\":[\"copy-scene\",\"set-transform\",\"reparent-object\",\"delete-object\"]}\n-->"}'
run_guard >/dev/null
[[ -e "$FAKE_RUNNER_CALLS" ]] || { echo "supported new-scene capability should reach Unity health preflight" >&2; exit 1; }
jq -e '.tier == "new-scene-composition"' "$TMP/state/authoring/GH-142.json" >/dev/null
jq -e '.status == "supported" and .authorizationTier == "new-scene-composition"' "$TMP/state/structural-authoring-preflight/GH-142.json" >/dev/null

# Any combination of scene-authoring labels fails closed.
reset_state
export FAKE_LABELS_JSON='[{"name":"resource:unity-editor"},{"name":"validation:unity-required"},{"name":"authoring:scene-structural"},{"name":"authoring:scene-new-composition"}]'
export FAKE_ISSUE_JSON='{"state":"open","body":""}'
set +e
run_guard >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 75 ]] || { echo "expected conflicting authoring labels to exit 75, got $status" >&2; exit 1; }
[[ ! -e "$FAKE_RUNNER_CALLS" ]] || { echo "conflicting authoring labels must halt before Unity health" >&2; exit 1; }

# New-scene composition without explicit requirements is not treated as a legacy authorization.
reset_state
export FAKE_LABELS_JSON='[{"name":"resource:unity-editor"},{"name":"validation:unity-required"},{"name":"authoring:scene-new-composition"}]'
export FAKE_ISSUE_JSON='{"state":"open","body":"No authoring requirements."}'
set +e
run_guard >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 78 ]] || { echo "expected undeclared new-scene requirements to exit 78, got $status" >&2; exit 1; }
[[ ! -f "$TMP/state/authoring/GH-142.json" ]] || { echo "undeclared new-scene issue must not receive an authoring receipt" >&2; exit 1; }

echo "structural-authoring-guard-test: PASS"
