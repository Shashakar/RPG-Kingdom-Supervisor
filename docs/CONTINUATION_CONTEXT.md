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

The refresh is deliberately conservative:

- it runs only when the durable `.symphony-attempt-complete` marker exists;
- it verifies the GH issue workspace and RPG Kingdom origin;
- it refuses to discard uncommitted source changes, local-only commits, divergent local/remote branches, or detached state;
- it fetches current remote refs host-side;
- if the durable remote feature branch is behind `origin/main`, it merges `origin/main` locally so the eventual handoff remains a fast-forward update to the existing remote feature branch;
- if that merge conflicts, it aborts the merge and fails closed before Codex starts rather than launching against stale assets;
- when `.gitattributes` declares Git LFS filters, it fetches `main`/feature LFS objects and runs `git lfs checkout` before Unity validation.

This keeps Git metadata/network synchronization host-owned while preserving work that has not yet reached the remote branch. A conflicting stale PR is therefore a human/review decision: supersede or resolve that branch, then explicitly rearm again.

## Validation

`tests/continuation-context-test.sh` uses a fake GitHub CLI to verify that:

- checked-in RPG Kingdom instructions are preserved;
- the current PR and head are included;
- review submission bodies survive across attempt boundaries;
- unresolved inline feedback is included while resolved threads are omitted;
- only issue and PR conversation comments newer than the previous attempt marker are included;
- the generated override is ignored by Git;
- a fresh workspace removes the generated override.

`tests/rearmed-workspace-refresh-test.sh` uses local Git repositories to verify that a clean durable continuation absorbs current `main`, while dirty work, unpushed commits, and merge conflicts are preserved/fail closed rather than being silently rewritten.
