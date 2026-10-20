# Provider routing — Phase 1

Phase 1 adds deterministic provider recommendations and blocks incorrect Codex launches. It does **not** add a Claude Code subprocess, Claude telemetry, or multi-repository execution.

## Labels and precedence

| Label | Meaning |
| --- | --- |
| `agent:codex` | Explicit operator override: Codex |
| `agent:claude` | Explicit operator override: Claude, manual only |
| `agent:auto` | Use classification instead of an override; also the behavior when no agent label exists |
| `work:deterministic` | Engineering, tests, CI, refactoring, tooling, data validation: Codex |
| `work:visual-unity` | Visual scene/terrain/asset/animation/lighting judgment: Claude |

Policy evaluation order:

1. `agent:codex` or `agent:claude` overrides automatic classification.
2. Otherwise, `work:deterministic` recommends Codex and `work:visual-unity` recommends Claude.
3. Otherwise, `authoring:scene-existing-composition` or `authoring:scene-new-composition` recommends Claude.
4. Otherwise, choose Codex. This preserves dispatch for existing, unlabeled RPG Kingdom issues.

More than one `agent:*` label or more than one `work:*` label is an error, including with an explicit override. `resource:unity-editor` and `validation:unity-required` **do not** imply Claude by themselves: mechanical Unity test work can be handled by Codex. Mixed tasks should be split into bounded engineering and visual work before dispatch when practical.

The resolver is `rpgk_select_provider` in `scripts/routing-policy.sh`. It prints tab-separated `provider`, `source`, `reason` values. For an existing issue:

```bash
source scripts/routing-policy.sh
labels="$(gh issue view 123 --repo Shashakar/RPG-Kingdom --json labels --jq '.labels[].name')"
rpgk_select_provider "$labels"
```

The existing `rpgk_select_route` then chooses the Codex Luna/Sol model and effort independently when Codex is selected. The provider label never grants extra scene-authoring, Unity, or tool permissions.

## Operator workflow

**Codex:** Label the issue for the intended work and/or explicitly choose `agent:codex`, then add `symphony:ready` when it is ready to execute. Existing model, quota, worker-lifetime, Unity resource, rework, PR, and human merge controls remain unchanged.

**Claude (manual):** Label with `agent:claude`, `work:visual-unity`, or applicable visual composition authoring classification. Do **not** add `symphony:ready`; that lease is for the existing Codex dispatcher. Provide Claude a separate approved workspace/branch, the correct game repository and its `AGENTS.md`, acceptance criteria and validation requirements. Claude creates a PR for normal human review; the operator tracks completion in GitHub.

If `symphony:ready` is accidentally applied to a Claude-selected issue, the Codex launcher refuses the issue *before* Codex initialization or usage telemetry. Normal failed-dispatch cleanup may mark it halted; review the labels and use the existing explicit rearm flow if a subsequent Codex run is intended. To intentionally force Codex for a visual issue, set `agent:codex` without another `agent:*` label. This is an override of routing only, not of production-scene access rules or human visual approval.

**Repository scope:** Current Supervisor execution remains targeted at `Shashakar/RPG-Kingdom`. Do not repoint `RPGK_REPO` globally to `Shashakar/Rekindle-Claude` or assume the isolated workspace rules transfer. Multi-repository support belongs in a separate implementation.

## Verification

```bash
bash tests/routing-policy-test.sh
bash tests/codex-router-test.sh
bash tests/run.sh
```

Tests exercise legacy defaults, explicit overrides, classifications, existing Unity composition hints, Unity resource-only tasks, conflicts, and failure to dispatch Claude via Codex.

## Deliberately deferred

Claude Code execution/monitoring, Claude-specific sandbox permissions, allowance telemetry, automatic provider fallback, provider-aware workspace provisioning across repositories, cross-provider reviews, and any automatic merge behavior.
