# Claude as a first-class Supervisor execution provider

Claude and Codex use **the same issue scope, authorization labels, Unity readiness,
host resource ownership, Git publication, independent review, two-cycle repair
policy, and human merge approval**. Provider routing is an implementation detail,
not an additional permission or operator-approval boundary.

## How to assign an issue

For a Codex task, use `agent:codex` and `symphony:ready`.
For a Claude task, use `agent:claude` and `symphony:claude-ready`.
The distinct ready labels prevent two consumers from claiming the same issue;
they are **not different approval thresholds**. Normal risk/model/effort labels
route the provider. The code-only/Unity/scene scope is expressed by the **same**
`resource:unity-editor`, `validation:unity-required` or
`validation:unity-optional`, and `authoring:*` labels.

There is **no separate visual authority JSON block**, Claude-only manifest
requirement, screenshot consent environment switch, or Claude-only host opt-in.
The issue body is the scope of work, subject to the target repository's
`AGENTS.md` and normal human review. An agent can modify an authorized
new preview scene; it need not stop at describing an arrangement in JSON.

### Standard execution

- The Rekindle service launches the Claude dispatcher by default. The
  `symphony:claude-ready` label is the explicit per-issue authorization. For
  emergency maintenance, `RPGK_CLAUDE_AUTONOMOUS=0` disables the Claude
  dispatcher only.
- Claude uses its ordinary editing, shell/test, subagent, skill, configured MCP,
  and research tools. Codex and Claude both have the same host-side Git broker
  for branch prepare, commits, push, and draft PR handoff.
- The provider's authenticated CLI must be installed for that host user; the
  host does not inject its tracker PAT into either worker. No extra login/
  enrollment step is required once the CLI is authenticated.
- Code/test/doc tasks may start immediately if the issue is ready. Unity tasks
  use the shared Rekindle host readiness setting
  `RPGK_REKINDLE_UNITY_READY`. If that shared host is unavailable, **both**
  providers are blocked from Unity-required/editor-owned work. This is a
  real host capability, not a provider-specific approval. Use
  `validation:unity-optional` for genuine work that can proceed without running
  Unity, with missing validation reported honestly.
- `resource:unity-editor` uses the same `locks/unity-editor.lock`
  ownership protocol. `validation:unity-required` requires actual fresh
  passing Unity evidence at host Git handoff for either provider.
- Failed attempts retain a clean audit trail; review can request the same
  bounded automatic repairs for Claude or Codex. **Neither auto-merges**.

## Visual work and licensed assets

A Claude issue such as *compare Blink vs Swordman with Kevin Iglesias
animations, then stage a Townsmith/Fantasy Adventure Environment preview* is
ordinary visual/Unity work. Specify the actual deliverables in the issue,
including the new isolated preview scene, screenshots, animation evidence,
comparison, and recommendation. Use `work:visual-unity` for model routing,
not as a different authorization class.

Visuals can be staged locally without committing purchased source assets.
The shared Git-index license boundary and normal target repo requirements
still apply to both providers. Source asset installation, Unity readiness,
production-scene modifications, actual test status and visual review must not be
fabricated. New preview scenes remain separate from production Hearthmere;
production-scene edits require explicit issue scope, just as for Codex.

The older `claude-visual-policy.py` and `claude-visual-host.py` are retained
only as experimental tools for existing test fixtures. **They are no longer
invoked or required by autonomous Claude dispatch.** The specialized
`RPGK_CLAUDE_VISUAL_*` switches and `rpgk-claude-visual-authority` issue
block are not part of the supported normal workflow.

## Host operations

After merging and deploying the Supervisor change, confirm:

```bash
cd ~/src/RPG-Kingdom-Supervisor
claude --version
claude auth status
gh auth status
sudo systemctl status rpg-kingdom-supervisor.service --no-pager
tail -n 60 ~/.local/state/rekindle-supervisor/claude-dispatcher.log
```

No dedicated Claude autonomy drop-in is necessary. If a previously installed
`95-claude-autonomous.conf` sets `RPGK_CLAUDE_AUTONOMOUS=1`, it may remain:
that value is redundant, but does not impose a second gate. A value of
`RPGK_CLAUDE_AUTONOMOUS=0` intentionally disables the dispatcher.

To start work, create/assign one task using the normal issue labels above.
The dispatcher polls every 30 seconds. Watch the resulting issue branch, draft
PR, review status, and diagnostics. Continue code work in parallel with
Supervisor maintenance; do not restart a host that has an active worker.

## Validation

```bash
python3 tests/claude-dispatcher-test.py
python3 tests/claude-worker-pilot-test.py
python3 tests/rekindle-migration-test.py
bash tests/run.sh
```

The CI unit tests cannot substitute for running a real Unity host test.
