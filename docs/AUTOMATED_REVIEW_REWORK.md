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

The review sidecar launched by `scripts/run-symphony.sh` watches that state and starts a distinct, read-only Codex reviewer.

Codex concurrency is role-aware:

```text
mutation slot: implementation | repair | report-only   capacity 1
review slot:   independent review                      capacity 1
maximum concurrent Codex processes                    2
```

The mutation and review slots use separate host `flock` files, so review for issue A may run while implementation/repair for unrelated issue B is active. Both roles also hold a per-issue lock for the entire Codex lifetime. A reviewer therefore never reads a workspace while that same issue is still being mutated. If the issue lock is already owned, review exits with a retryable deferral before starting Codex or worker telemetry; the issue remains in `symphony:agent-review` for the next poll.

Implementation/repair/report-only still share one mutation slot, so this change does **not** permit two mutating Codex workers. Review remains read-only and does not use Unity or Git mutation interfaces.

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

The orchestrator scans only `symphony:agent-review`. That label remains present until a replacement repair dispatch is fully established, so an interrupted persisted rework transition can be resumed after restart without polling active `symphony:rework` lifetimes. This avoids redispatch races during the legitimate gap after a repair handoff removes `symphony:ready` and before `after_run` queues the next review. The `.symphony-attempt-complete` timestamp distinguishes an interrupted transition from a genuinely completed repair lifetime that requires fresh re-review.

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

## Concurrency and usage accounting

Per-thread token counts remain attributable because implementation and review use distinct Codex rollouts/workspaces and same-issue overlap is prohibited.

Codex quota percentages are account-global, however. When two worker lifetimes overlap, the quota movement observed between either worker's before/after snapshots includes activity from both workers. Supervisor usage analysis therefore excludes overlapping lifetimes from **per-worker quota-cost** medians/rankings instead of pretending the shared account change belongs to either worker. Token usage remains available.

Worker detail exposes the overlap relationship and treats the per-worker quota delta as unavailable for attribution while preserving the raw observed before/after account snapshots for diagnostics.

## Dashboard

The localhost Supervisor dashboard displays active worker lifetimes with their issue and role. With role-aware concurrency, the active list may legitimately contain one mutation worker and one reviewer at the same time. That pair is the maximum supported Codex concurrency under this policy.

The dashboard also displays:

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

Restart Supervisor so `scripts/run-symphony.sh` and the review sidecar use the new role-aware lock policy.

For a controlled smoke test, use an implementation-ready RPG Kingdom issue and allow it to reach PR handoff without manually adding review/rearm labels. Expected autonomous path for a clean PR:

```text
symphony:ready
-> implementation handoff
-> symphony:agent-review
-> structured independent review
-> symphony:human-review
```

With a second unrelated implementation issue ready at the same time, the reviewer and that implementation worker may now run concurrently. A reviewer must never overlap mutation of its own issue/workspace.

For a deliberately repairable review finding, expected path is:

```text
agent-review -> rework + rearm + ready -> repair -> agent-review -> human-review
```

Do not merge automatically; human approval remains the final gate.
