# Supervisor Setup

This document covers the Phase 1 operator setup for running official OpenAI Symphony against `Shashakar/RPG-Kingdom` using this repository's `WORKFLOW.md`.

## 1. Host environment

Official Symphony currently publishes macOS and Linux release targets. On Windows, use WSL2 for the initial setup unless you intentionally choose to build and support the Elixir runtime another way.

The Symphony host needs:

- Git
- GitHub CLI (`gh`) or another Git authentication path that can push to `Shashakar/RPG-Kingdom`
- Codex CLI with App Server support
- `mise`
- network access to GitHub and OpenAI

Verify from the same shell that will run Symphony:

```bash
git --version
gh --version
codex --version
mise --version
```

Also verify that normal repository operations work from that shell before involving Symphony:

```bash
git clone https://github.com/Shashakar/RPG-Kingdom.git /tmp/rpg-kingdom-supervisor-auth-check
cd /tmp/rpg-kingdom-supervisor-auth-check
git remote -v
gh auth status
cd ..
rm -rf rpg-kingdom-supervisor-auth-check
```

Do not proceed to unattended workers until the operator environment can authenticate to GitHub reliably.

## 2. Install the evaluated Symphony revision

The currently evaluated upstream revision is recorded in `SYMPHONY_UPSTREAM.md`.

```bash
git clone https://github.com/openai/symphony.git ~/src/openai-symphony
cd ~/src/openai-symphony
git checkout 8001b52e3062495a16e520e4ceaf8f9de868c4d0
cd elixir
mise trust
mise install
mise exec -- mix setup
mise exec -- mix build
```

Do not silently replace the pinned revision with current upstream `main`. Review and deliberately update the pin first.

## 3. Configure GitHub tracker authentication

Symphony's GitHub tracker adapter reads the repository configured in `WORKFLOW.md` and uses `GITHUB_TOKEN` by default.

Set a host-side token with the minimum repository permissions needed for GitHub Issues operations used by the workflow. Do not commit it to either repository.

For a shell session:

```bash
export GITHUB_TOKEN='...'
```

The evaluated Symphony revision intentionally scrubs known GitHub tracker-token aliases from the Codex child environment. The worker receives the provider-native `github_api` tool instead of direct access to the tracker credential.

### Separate Git push/PR authentication

The tracker token is not the worker's Git credential.

Codex still needs the normal operator environment to support branch push and PR creation. Configure Git/GitHub CLI authentication independently and verify it in the same WSL/Linux environment used by Symphony.

Do not weaken the tracker credential boundary just to make `git push` easier.

## 4. Create the dispatch label

Create this label once in `Shashakar/RPG-Kingdom`:

```text
symphony:ready
```

Phase 1 uses this single label as an explicit opt-in lease. An open issue is dispatchable only while it has this label.

Do not add it to important implementation issues until the smoke path is ready to run.

## 5. Clone this supervisor repository

```bash
git clone https://github.com/Shashakar/RPG-Kingdom-Supervisor.git ~/src/RPG-Kingdom-Supervisor
cd ~/src/RPG-Kingdom-Supervisor
```

Use the merged `main` version of `WORKFLOW.md` for the smoke test.

## 6. Start Symphony

From the installed Symphony `elixir` directory:

```bash
mise exec -- ./bin/symphony \
  --i-understand-that-this-will-be-running-without-the-usual-guardrails \
  ~/src/RPG-Kingdom-Supervisor/WORKFLOW.md
```

Optionally enable the observability service with a port:

```bash
mise exec -- ./bin/symphony \
  --i-understand-that-this-will-be-running-without-the-usual-guardrails \
  --port 4000 \
  ~/src/RPG-Kingdom-Supervisor/WORKFLOW.md
```

The workflow currently allows exactly one concurrent Codex worker.

## 7. Phase 1 smoke test

Choose or create a deliberately low-risk RPG Kingdom issue. Good smoke-test work is:

- small;
- code/documentation only;
- easy to validate;
- not architecturally sensitive;
- not dependent on production-scene authoring;
- safe to close or discard if orchestration fails.

Before dispatch, make sure the issue itself is complete enough that a competent engineer could implement it without a conversation.

Then add:

```text
symphony:ready
```

Expected flow:

1. Symphony sees the open labeled issue.
2. It creates an isolated workspace.
3. The `after_create` hook clones RPG Kingdom into that workspace.
4. Symphony launches Codex through App Server.
5. Codex reads RPG Kingdom's checked-in instructions.
6. Codex creates a `codex/` branch, implements, validates, pushes, and opens a PR.
7. Only after the PR exists, Codex removes `symphony:ready` from the issue.
8. The issue remains open and the PR waits for human/ChatGPT review.

## 8. Failure behavior

For the first smoke test, do not hide or aggressively auto-recover failures.

If execution fails:

- retain the relevant Symphony logs and workspace long enough to inspect them;
- do not manually patch the workspace before understanding the failure;
- record whether the problem belongs to authentication, workspace creation, Codex/App Server, RPG Kingdom instructions, Git/PR publication, or the workflow contract;
- fix the supervisor layer only when the failure is genuinely orchestration-related.

A gameplay/test failure discovered by Codex is not automatically a supervisor bug.

## 9. What Phase 1 intentionally does not configure

The following are deferred until the smoke path is reliable:

- Sol/Terra/Luna routing;
- risk classification;
- more than one concurrent worker;
- Unity Editor resource locks;
- automatic Unity execution;
- automatic merge;
- automatic PR feedback re-dispatch.
