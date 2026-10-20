---
tracker:
  kind: github
  provider:
    repo: Shashakar/Rekindle-Claude
    token: $SYMPHONY_GITHUB_TOKEN
    api_url: https://api.github.com
  required_labels:
    - symphony:ready
  active_states:
    - open
  terminal_states:
    - closed
polling:
  interval_ms: 15000
workspace:
  root: ~/code/rekindle-symphony-workspaces
hooks:
  timeout_ms: 600000
  after_create: |
    git clone --branch rekindle-vertical-slice --single-branch https://github.com/Shashakar/Rekindle-Claude.git .
  before_run: |
    bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/rekindle-migration-preflight.sh" &&
    bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/before-run-guard.sh" &&
    bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/unity-resource-guard.sh"
  after_run: |
    bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/release-unity-resource.sh"
    bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/after-run-guard.sh"
agent:
  max_concurrent_agents: 1
  max_turns: 4
codex:
  command: bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/codex-app-server-router.sh"
  approval_policy: never
  permissions: rpgk_supervisor_workspace
  read_timeout_ms: 120000
  stall_timeout_ms: 660000
---

You are the bounded Codex implementation worker for Rekindle issue {{ issue.identifier }}.

Issue: {{ issue.url }}
Title: {{ issue.title }}
Labels: {{ issue.labels }}

{% if issue.description %}
{{ issue.description }}
{% else %}
No description was provided.
{% endif %}

## Repository and safety

- Work only in this isolated Shashakar/Rekindle-Claude workspace, never in RPG Kingdom or Supervisor.
- The integration base is `rekindle-vertical-slice`, **not main**. Read `AGENTS.md`, `README.md`, `Docs/ARCHITECTURE.md`, and only the relevant subsystem docs/source.
- Do not directly modify the production Hearthmere scene or regenerate the world without explicit issue approval. Do not run PlayMode/full-slice tests until real-save isolation is proven.
- Unity Editor/scene authoring is disabled in this migration until the target-specific validation gate is approved.
- Claude-selected tasks are manual-pilot work in Phase 2; this Codex launcher cannot handle them.

## Worker workflow

Before writing source, request your issue branch from the host Git broker:

    bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/git-handoff.sh" prepare --branch "codex/gh-{{ issue.identifier }}-implementation"

Implement the issue with bounded scope, tests, and evidence. For finished implementation request host commit, push, and **draft PR**:

    bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/git-handoff.sh" handoff \
      --branch "codex/gh-{{ issue.identifier }}-implementation" \
      --commit-message "Implement {{ issue.identifier }}" \
      --pr-title "{{ issue.identifier }}: {{ issue.title }}"

Use host broker interfaces for Git state; don't mutate `.git` yourself. A passing compilation is not visual acceptance.
Report exact limitations and tests. If blocked, preserve worker status and allow host halt handling. Never remove the dispatch lease directly and never merge a PR.

## Provider and human gate

Provider selection is separate from Codex Luna/Sol model choice. Explicit provider override does not grant scene permissions.
All PRs require human final approval. No automated creative sign-off or merge.
