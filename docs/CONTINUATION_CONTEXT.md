# Reviewed continuation context

RPG Kingdom issues can be rearmed after a previous bounded worker lifetime. The original GitHub issue description is not sufficient context for reviewed continuations because actionable feedback may live in PR reviews, unresolved inline threads, or issue/PR comments added after the previous attempt.

Before Codex launches, `scripts/codex-app-server-router.sh` runs `scripts/build-continuation-context.sh` while `SYMPHONY_GITHUB_TOKEN` is still host-side. Fresh workspaces do not receive any override. Rearmed workspaces with `.symphony-attempt-complete` receive a temporary root `AGENTS.override.md`.

Codex gives `AGENTS.override.md` precedence over `AGENTS.md`, so the generated file first embeds the checked-in repository `AGENTS.md` verbatim and then appends bounded continuation context:

- the previous worker completion timestamp;
- the current workspace branch;
- the existing open PR for that branch, including head SHA and PR description;
- current PR review submission bodies;
- unresolved inline review threads;
- PR conversation comments newer than the previous worker completion marker;
- issue comments newer than the previous worker completion marker.

This keeps older issue chatter out of the worker context while retaining review blockers that may have been posted before the immediately previous attempt.

The generated `AGENTS.override.md` is Supervisor-owned runtime state. The builder adds it to the workspace-local `.git/info/exclude`, never to the repository `.gitignore`. If RPG Kingdom ever tracks its own `AGENTS.override.md`, the builder fails closed rather than overwriting repository instructions. Fresh workspaces remove any stale generated override.

Tracker credentials remain host-side. The context is rendered before the router unsets `SYMPHONY_GITHUB_TOKEN`; the Codex child receives the rendered instructions but not the GitHub tracker credential.

## Rearmed workspace refresh

A reviewed continuation must also start from repository state that includes changes merged to `main` while the prior worker was halted. After `scripts/before-run-guard.sh` verifies and consumes `symphony:rearm`, it runs `scripts/refresh-rearmed-workspace.sh` before Unity preflight or Codex startup.

The issue-side refresh script performs only read-only branch discovery and submits the existing `git-handoff.sh prepare` request. `scripts/run-symphony.sh` launches the Git broker through `scripts/git-handoff-host-wrapper.py`; for a rearmed workspace already on its durable `codex/*` branch, that host wrapper reconciles the checkout before delegating to the normal handoff host. This is required because Symphony/Codex workspace permissions intentionally protect `.git` metadata from issue-side processes.

The host-owned reconciliation is deliberately conservative:

- it runs only when the durable `.symphony-attempt-complete` marker exists and the checkout is already on the requested durable `codex/*` branch;
- it refuses to discard uncommitted source changes, local-only commits, or divergent local/remote branch history;
- it fetches current remote refs with host-owned Git/network permissions;
- when the local continuation is behind its durable remote branch, it fast-forwards to that remote branch;
- if the durable branch is behind `origin/main`, it merges current `origin/main` locally so final handoff can remain a fast-forward update;
- if that merge conflicts, it aborts and fails closed before Codex starts;
- when `.gitattributes` uses Git LFS, it fetches the current main/feature objects and hydrates the checkout before Unity validation.

This preserves the protected model-side Git boundary while ensuring reviewed workers do not validate stale repository or asset state. A dirty, unpushed, divergent, or conflicting continuation remains a human/review decision instead of being silently rewritten.

## Validation

`tests/continuation-context-test.sh` uses a fake GitHub CLI to verify that:

- checked-in RPG Kingdom instructions are preserved;
- the current PR and head are included;
- review submission bodies survive across attempt boundaries;
- unresolved inline feedback is included while resolved threads are omitted;
- only issue and PR conversation comments newer than the previous attempt marker are included;
- the generated override is ignored by Git;
- a fresh workspace removes the generated override.

`tests/git-handoff-host-wrapper-test.py` uses local Git repositories to reproduce the GH-98 shape: a stale local feature checkout whose durable remote branch has already advanced to include newer `main`. It verifies that host preparation fast-forwards to the durable branch and preserves/fails closed on dirty or local-only work.
