# Unity editor resource lock recovery

Supervisor holds `~/.local/state/rpg-kingdom-supervisor/locks/unity-editor.lock` for the lifetime of any Symphony worker that owns `resource:unity-editor`.

Normal ownership is conservative: an idle Unity broker does **not** imply that the lease is stale, because a live worker may legitimately hold the editor between validation runs.

When a new dispatch encounters an existing lock, `unity-resource-guard.sh` reclaims it only when both of these are true:

1. the Unity broker reports protocol version 1, `state: ready`, and `activeRequest: null`; and
2. the recorded `GH-N` owner issue is provably no longer dispatch-active: it is closed, or it is open without `symphony:ready` and is explicitly in one of the terminal/hand-off lifecycle states (`symphony:halted`, `symphony:agent-review`, `symphony:human-review`, or `symphony:human-attention`).

If either signal is missing, contradictory, unavailable, or ambiguous, Supervisor keeps the existing lock and fails closed exactly as before.

A proven stale lock is reclaimed with an atomic directory rename before the new lease is created. If another contender changes the lock during reconciliation, acquisition is retried against the new state rather than assuming ownership. This prevents two workers from both succeeding after stale-lock recovery.

The guard logs whether a lock was retained because the broker was active/ambiguous, retained because the owner could not be proven inactive, or successfully recovered as stale.

Deterministic coverage lives in `tests/unity-resource-guard-test.sh` and exercises active ownership, closed/halted stale ownership, ambiguous broker state, and two-contender recovery.
