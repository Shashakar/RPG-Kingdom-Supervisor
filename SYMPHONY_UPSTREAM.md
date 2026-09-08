# Symphony Upstream Pin

The supervisor is currently evaluated against:

- Repository: `openai/symphony`
- Commit: `8001b52e3062495a16e520e4ceaf8f9de868c4d0`
- Commit date: 2026-08-12
- Commit subject: `Scrub GitHub and GitLab authentication token aliases (#119)`

## Why this revision

This revision includes the official GitHub Issues tracker adapter and Codex App Server runtime while also preserving the intended credential boundary: tracker authentication remains host-side and known GitHub/GitLab tracker token aliases are scrubbed from Codex child environments.

## Upgrade policy

Do not follow upstream `main` implicitly in production use.

Before changing this pin:

1. review upstream changes since the current revision;
2. verify GitHub tracker behavior and Codex App Server configuration remain compatible with `WORKFLOW.md`;
3. verify credential isolation has not regressed;
4. run the Phase 1 smoke path against a disposable or low-risk RPG Kingdom issue;
5. update this file with the new revision and relevant compatibility notes.

This file records the revision we have evaluated; it does not vendor Symphony source code into this repository.
