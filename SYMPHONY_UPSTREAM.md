# Symphony Upstream Pin

The supervisor is currently evaluated against:

- Repository: `openai/symphony`
- Commit: `8001b52e3062495a16e520e4ceaf8f9de868c4d0`
- Commit date: 2026-08-12
- Commit subject: `Scrub GitHub and GitLab authentication token aliases (#119)`
- Local compatibility branch: `rpgk/named-permissions`
- Tracked compatibility transforms:
  - `scripts/patch-symphony-named-permissions.py`
  - `scripts/patch-symphony-usage-limit.py`

## Why this revision

This revision includes the official GitHub Issues tracker adapter and Codex App Server runtime while also preserving the intended credential boundary: tracker authentication remains host-side and known GitHub/GitLab tracker token aliases are scrubbed from Codex child environments.

## Named-permissions compatibility

The pinned Symphony revision always supplies the legacy Codex sandbox selection on App Server `thread/start` / `turn/start`. Current Codex permission profiles can explicitly make the current workspace's `.git` metadata writable, but those legacy request fields override the App Server's selected named profile and return `.git` to read-only protection.

The Supervisor therefore carries a narrow local compatibility transform that adds a `codex.permissions` workflow setting and forwards it through the App Server protocol's named `permissions` field. Legacy Symphony sandbox behavior remains the fallback when the setting is absent. The transform exists because the required boundary cannot be expressed through the pinned upstream configuration contract alone.

## Usage-limit compatibility

The pinned Symphony revision treats every App Server `turn/completed` result as a normal completed turn. When Codex reports structured `codex_error_info: usage_limit_exceeded`, that behavior causes `AgentRunner` to launch continuation turns until `agent.max_turns` is exhausted, even though no useful implementation work can proceed.

The Supervisor therefore carries a second narrow compatibility transform that:

- recognizes structured `usage_limit_exceeded` terminal metadata on the completed turn;
- preserves the reported message/retry time when present;
- ends the current worker lifetime without dispatching continuation turns;
- writes a workspace-local `.symphony-usage-limit.json` handoff marker for the host `after_run` guard;
- lets the host remove `symphony:ready`, add `symphony:halted`, and report quota exhaustion distinctly from genuine turn-budget exhaustion.

The quota marker is runtime-only state and is cleared before each Codex session and after successful host reporting. Reviewed rearm semantics remain unchanged.

## Applying compatibility

Apply and validate both transforms through the Supervisor scripts; do not hand-edit the upstream checkout:

```bash
bash scripts/apply-symphony-permissions-patch.sh
bash scripts/verify-symphony-permissions-patch.sh
```

The generated local compatibility branch remains derived from the evaluated pin. If upstream Symphony adds equivalent first-class support for either compatibility seam, remove the corresponding transform as part of the reviewed pin upgrade.

## Upgrade policy

Do not follow upstream `main` implicitly in production use.

Before changing this pin:

1. review upstream changes since the current revision;
2. verify GitHub tracker behavior and Codex App Server configuration remain compatible with `WORKFLOW.md`;
3. verify credential isolation has not regressed;
4. determine whether upstream now provides the named-permissions and usage-limit seams and retire/rebase local compatibility transforms accordingly;
5. run the Supervisor shell suite plus compatibility verification;
6. run the Phase 1 smoke path against a disposable or low-risk RPG Kingdom issue;
7. update this file with the new revision and relevant compatibility notes.

This repository does not vendor Symphony source. It pins the evaluated upstream revision and, while necessary, tracks minimal deterministic source transforms applied to a local compatibility branch.
