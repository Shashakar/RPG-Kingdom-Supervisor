# Automated Review and Bounded Rework

## Purpose

Supervisor owns the quality loop that follows a successful Symphony implementation handoff:

```text
implementation -> independent review -> bounded repair -> re-review -> human review
```

Humans still approve issue intent and final integration. No agent may merge to `main`.

## Lifecycle

A successful Git handoff removes `symphony:ready`. During `after_run`, Supervisor confirms the issue workspace branch has an open PR and adds:

```text
symphony:agent-review
```

The review sidecar launched by `scripts/run-symphony.sh` watches that state and starts a distinct, read-only Codex reviewer. Reviewer and implementation sessions share `~/.local/state/rpg-kingdom-supervisor/locks/codex-session.lock`, preserving the current one-agent-at-a-time budget.

The reviewer uses `codex exec --sandbox read-only --output-schema ...` and must emit the schema in `schemas/review-verdict.schema.json`:

- `approved`
- `changes_required`
- `blocked_or_ambiguous`

The reviewer cannot mutate the RPG Kingdom workspace or GitHub and does not repair findings in the review session.

## Durable state

Every review transition is persisted in an RPG Kingdom issue comment containing a hidden `rpgk-review-state` JSON marker. The state contains:

- current lifecycle state;
- PR/head identity;
- review cycle;
- repairs consumed / maximum repairs;
- latest verdict and reason;
- reviewer/repair routing information;
- complete review history and findings.

GitHub labels provide the current operator-visible state:

- `symphony:agent-review`
- `symphony:rework`
- `symphony:human-review`
- `symphony:human-attention`

Run `scripts/install-labels.sh` after deploying this feature so the RPG Kingdom repository has those labels plus the `repair-route:*` labels.

The orchestrator scans both `agent-review` and `rework` issues. A persisted rework decision whose label transition was interrupted by a Supervisor restart is resumed rather than charged as another review/repair cycle. The `.symphony-attempt-complete` timestamp distinguishes an interrupted transition from a genuinely completed repair lifetime that requires fresh re-review.

## Approval

`approved` moves the issue to:

```text
symphony:human-review
```

Supervisor posts a Human Review packet to the PR with:

- issue / PR / current head;
- changed files;
- automated review count;
- repair count;
- review history;
- prior findings repaired by the loop;
- the PR's implementation and validation notes.

No merge endpoint exists in the review orchestrator. A human performs the integration decision.

## Changes required

`changes_required` persists the reviewer findings before dispatch mutation, then transitions the existing issue/PR to:

```text
symphony:rework
symphony:rearm
symphony:ready
```

`rearm` is added before `ready`, preserving the reviewed-continuation contract. The next Symphony worker updates the same workspace/branch/PR and receives the durable review comment through the existing continuation-context builder.

### Fresh repair routing

The reviewer may recommend:

- `repair-route:luna`
- `repair-route:terra`
- `repair-route:sol`
- `repair-route:astra`

These labels are advisory and valid only while `symphony:rework` is active. The normal `model:*` operator override remains higher precedence. This makes review findings new routing evidence without overriding explicit human routing.

After repair handoff, `after_run` removes transient rework/repair-route labels and queues a new independent review.

## Repair budget

Default automatic repair budget:

```text
RPGK_MAX_AUTOMATIC_REPAIRS=2
```

This allows three automated review passes at most:

```text
Review 1 -> Repair 1 -> Review 2 -> Repair 2 -> Review 3
```

If Review 3 still returns `changes_required`, automation moves to:

```text
symphony:human-attention
reason: review_loop_exhausted
```

No third repair is dispatched.

## Scope and ambiguity safety valve

A reviewer returns `blocked_or_ambiguous` when correct resolution requires material work outside the approved issue scope or a product/design decision. Supervisor moves the issue to `symphony:human-attention` and does not dispatch speculative repair.

The review prompt explicitly prohibits weakening or rewriting acceptance criteria to manufacture approval.

## Reviewer routing

Default reviewer policy:

- mechanical / normal / investigative issue: Terra / medium;
- architecture / end-to-end issue: Sol / high.

Override for experiments with:

```bash
export RPGK_REVIEW_MODEL=gpt-5.6-sol
export RPGK_REVIEW_EFFORT=high
```

Reviewer cost is intentionally not optimized below the quality needed for an independent gate.

## Dashboard

The localhost Supervisor dashboard now displays:

- lifecycle state;
- review cycle;
- repairs consumed / maximum;
- latest verdict and summary;
- halt reason;
- routing recommendation;
- PR number;
- whether human action is required.

Unity run history remains available on the same dashboard for validating review evidence.

## Operational setup

After merging this feature:

```bash
cd ~/src/RPG-Kingdom-Supervisor
git pull
bash scripts/install-labels.sh
bash tests/run.sh
```

Restart Supervisor so `scripts/run-symphony.sh` launches the review sidecar.

For a controlled smoke test, use an implementation-ready RPG Kingdom issue and allow it to reach PR handoff without manually adding review/rearm labels. Expected autonomous path for a clean PR:

```text
symphony:ready
-> implementation handoff
-> symphony:agent-review
-> structured independent review
-> symphony:human-review
```

For a deliberately repairable review finding, expected path is:

```text
agent-review -> rework + rearm + ready -> repair -> agent-review -> human-review
```

Do not merge automatically; human approval remains the final gate.
