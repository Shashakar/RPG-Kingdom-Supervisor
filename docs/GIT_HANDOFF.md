# Host-Owned Git Handoff

## Why this exists

RPG Kingdom Symphony workers can reliably edit source files, but real model turns have intermittently observed protected `.git` metadata and unavailable GitHub DNS even when model-free Codex/App Server probes pass. RPG Kingdom #98 proved that this can strand a fully implemented, fully Unity-validated change before commit/push/PR handoff.

The Supervisor therefore treats final Git metadata/network mutation as a bounded host capability, parallel to the host-owned Unity boundary. This does **not** give the worker a generic host shell and it does **not** automate PR merge.

## Lifecycle

`scripts/run-symphony.sh` starts two host services before Symphony:

1. the Unity broker;
2. the Git handoff broker.

The Git broker writes lifecycle state to:

```text
~/.local/state/rpg-kingdom-supervisor/git-broker/status.json
```

The status includes broker state, active GH workspace/operation/branch, elapsed time, and the last handoff commit/PR result. This is intentionally structured so the diagnostics UI can consume it later without scraping terminal output.

## Worker interface

The worker uses only `scripts/git-handoff.sh`.

### Health

```bash
bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/git-handoff.sh" health
```

This validates that the current GH workspace is an RPG Kingdom Git repository reachable through the host broker.

### Prepare the task branch

Run before source edits:

```bash
bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/git-handoff.sh" prepare \
  --branch codex/gh-123-short-description
```

The host validates the workspace and origin, fetches `origin`, then safely creates/switches the requested `codex/*` branch. A dirty worktree on `main` is preserved when the branch switch is safe, which allows a halted attempt's valid source edits to be recovered without temporary Git metadata copies.

The host rejects unrelated active branches and never force-resets the worktree.

#### Reviewed continuation refresh

When a one-shot rearm resumes an existing clean `codex/*` workspace, the local branch is the durable continuation state even if it has never been pushed. Host-owned prepare therefore refreshes that branch against current repository state before Codex continues:

- a missing `origin/<branch>` is valid for a local-only continuation;
- clean local commits ahead of `origin/<branch>` are preserved;
- a local branch behind `origin/<branch>` fast-forwards to the remote branch first;
- true local/remote feature-branch divergence fails closed;
- current `origin/main` is merged into the continuation branch when needed, preserving local history rather than rebasing it;
- dirty worktrees are never rewritten automatically;
- merge conflicts or later refresh failures restore the exact clean continuation HEAD that existed before refresh.

This lets an old preserved workspace acquire newly merged repository capabilities without requiring a premature feature-branch push merely to continue work.

### Complete the handoff

After implementation and required validation:

```bash
bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/git-handoff.sh" handoff \
  --branch codex/gh-123-short-description \
  --commit-message "fix: concise description" \
  --pr-title "Concise PR title" \
  --pr-body "Summary, validation evidence, setup, exclusions, and Closes #123" \
  --validation-run 20260910T010710Z-playmode-86713
```

Repeat `--validation-run` for additional successful Unity runs. A long PR description may instead be supplied with `--pr-body-file`.

For a reviewed continuation, the previous `.symphony-attempt-complete` marker is also the validation freshness boundary. Every supplied Unity summary must have been produced after that marker. Passing results from an earlier worker lifetime are intentionally rejected as `ValidationEvidenceStale`; rearm authorizes another attempt, not reuse of old evidence as proof of the new attempt.

## Safety policy

The host runner fails closed unless all applicable checks pass:

- the workspace is exactly `GH-N` under the configured Symphony workspace root;
- the Git top-level is that workspace;
- `origin` resolves to the configured RPG Kingdom GitHub repository;
- the target branch is a valid `codex/*` branch;
- reviewed continuation refresh preserves clean local-only or locally-ahead branch history and rejects true remote divergence;
- `origin/main` is an ancestor of the handoff commit;
- an existing remote feature branch can fast-forward to the local handoff commit;
- Supervisor runtime artifacts are not staged for commit;
- when the issue has `validation:unity-required`, at least one explicitly supplied Unity run resolves to a passing, non-zero `summary.json` result;
- when the workspace contains a prior completed-attempt marker, every supplied Unity run is newer than that marker;
- an existing PR is rewritten only when the remote feature branch advances or the handoff carries fresh current-attempt Unity evidence;
- push succeeds without force and the remote branch SHA verifies back to the local commit;
- the PR exists against `main` before the `symphony:ready` dispatch lease is removed.

A normal handoff operation may stage and commit the issue's source changes, but it will not rebase, force-push, delete branches, or mutate another repository. The reviewed-continuation **prepare** path may create a normal merge commit from current `origin/main` when required to refresh a clean preserved branch; it never rewrites the continuation's existing commits.

The existing-PR progress gate is deliberately independent from whether `git commit` happens during the current handoff call. A recovered workspace may already contain a valid local commit from an earlier failed network handoff; advancing the remote branch to that commit is real progress and permits the PR update. Conversely, if the remote branch is unchanged and no fresh validation exists, the host returns `NoHandoffProgress` instead of allowing a worker to rewrite the PR description with unsupported completion claims.

## Authentication

The host broker inherits `SYMPHONY_GITHUB_TOKEN` from the same secrets file used by Symphony. The token is used for GitHub REST operations. For Git HTTPS operations, the host runner installs a small `GIT_ASKPASS` helper under the Supervisor state directory; the helper reads the token from the process environment and does not write the token value to disk.

## Failure behavior

Broker/client results are structured and use explicit statuses such as:

- `InvalidWorkspace`
- `InvalidOrigin`
- `InvalidBranch`
- `UnexpectedBranch`
- `ContinuationSyncBlocked`
- `ValidationEvidenceMissing`
- `ValidationEvidenceStale`
- `ValidationEvidenceFailed`
- `NoHandoffProgress`
- `MainNotIntegrated`
- `NonFastForward`
- `GitFailed`
- `GitHubNetworkFailed`
- `PushVerificationFailed`
- `HostBusy`
- `TimedOut`

A failed handoff leaves the workspace intact for diagnosis/rearm. A failed reviewed-continuation refresh that began from a clean branch restores the original clean HEAD before returning `ContinuationSyncBlocked`. If the worker exits while `symphony:ready` still exists, the existing after-run budget guard continues to halt the issue and prevent automatic redispatch.

## Human review boundary

The broker stops at a reviewable PR. PR review and merge remain human-owned.