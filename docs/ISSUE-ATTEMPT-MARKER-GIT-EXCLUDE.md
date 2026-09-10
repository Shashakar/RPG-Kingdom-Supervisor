# Attempt-marker Git exclusion

The Supervisor-owned `.symphony-attempt-complete` marker is durable workspace state, not RPG Kingdom source. Rearmed worker lifetimes intentionally preserve it so the budget guard remains fail-closed after the one-shot `symphony:rearm` approval is consumed.

Before Codex starts, `before-run-guard.sh` registers the marker in the workspace-local Git exclude file (`git rev-parse --git-path info/exclude`). This keeps the untracked runtime artifact out of ordinary `git add -A` handoff staging without changing RPG Kingdom's checked-in `.gitignore`.

The Git handoff's existing `ForbiddenPath` validation remains authoritative. If the marker is already tracked or explicitly force-staged, it is still rejected rather than silently committed.

This closes the continuation failure exposed by RPG Kingdom issue #98 after the new rearm flow succeeded: implementation and Unity validation completed, but final handoff attempted to stage the preserved marker and failed with `ForbiddenPath`.
