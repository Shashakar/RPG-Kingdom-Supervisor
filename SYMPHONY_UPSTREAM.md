# Symphony Upstream Pin

The supervisor is currently evaluated against:

- Repository: `openai/symphony`
- Commit: `8001b52e3062495a16e520e4ceaf8f9de868c4d0`
- Commit date: 2026-08-12
- Commit subject: `Scrub GitHub and GitLab authentication token aliases (#119)`
- Local compatibility branch: `rpgk/named-permissions`
- Tracked compatibility transform: `scripts/patch-symphony-named-permissions.py`

## Why this revision

This revision includes the official GitHub Issues tracker adapter and Codex App Server runtime while also preserving the intended credential boundary: tracker authentication remains host-side and known GitHub/GitLab tracker token aliases are scrubbed from Codex child environments.

## Named-permissions compatibility

The pinned Symphony revision always supplies the legacy Codex sandbox selection on App Server `thread/start` / `turn/start`. Current Codex permission profiles can explicitly make the current workspace's `.git` metadata writable, but those legacy request fields override the App Server's selected named profile and return `.git` to read-only protection.

The Supervisor therefore carries one narrow local compatibility transform that adds a `codex.permissions` workflow setting and forwards it through the App Server protocol's named `permissions` field. Legacy Symphony sandbox behavior remains the fallback when the setting is absent. The transform exists because the required boundary cannot be expressed through the pinned upstream configuration contract alone.

Apply and validate it through the Supervisor scripts; do not hand-edit the upstream checkout:

```bash
bash scripts/apply-symphony-permissions-patch.sh
bash scripts/verify-symphony-permissions-patch.sh
```

If upstream Symphony adds equivalent first-class support, remove this transform as part of the reviewed pin upgrade.

## Upgrade policy

Do not follow upstream `main` implicitly in production use.

Before changing this pin:

1. review upstream changes since the current revision;
2. verify GitHub tracker behavior and Codex App Server configuration remain compatible with `WORKFLOW.md`;
3. verify credential isolation has not regressed;
4. determine whether upstream now provides the named-permissions seam and retire/rebase the local compatibility transform accordingly;
5. run the Supervisor shell suite plus both permission checks;
6. run the Phase 1 smoke path against a disposable or low-risk RPG Kingdom issue;
7. update this file with the new revision and relevant compatibility notes.

This repository does not vendor Symphony source. It pins the evaluated upstream revision and, while necessary, tracks the minimal deterministic source transform applied to a local compatibility branch.
