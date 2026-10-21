# Claude and Codex — Rekindle visual and Unity work

**Visual work is ordinary issue work, regardless of model/provider.** There is
no separate Claude visual permission lane. The relevant approval is the
authorized GitHub issue and its `agent:*`, `resource:*`, `validation:*` and
`authoring:*` labels. See [Claude/Codex dispatch parity](CLAUDE_AUTONOMOUS_DISPATCH.md).

## Standard visual experiment

For the planned Blink-versus-Swordman and Townsmith/Fantasy Adventure Environment
evaluation, write a GitHub issue asking for:
1. The same Kevin Iglesias idle/walk/combat clips on both candidate models,
   with comparable scale, lighting and gameplay-distance captures.
2. A small **new, isolated Unity preview scene** with a character, building,
   vegetation and props. Keep the original Hearthmere scene untouched.
3. Evidence screenshots, animation/asset-path notes, comparison, uncertainties,
   and a recommendation for **human** visual review.

Assign `agent:claude`, `symphony:claude-ready`, and
`work:visual-unity`. Use normal `risk:*`/`claude-model:*` labels to
choose reasoning effort. For work requiring an actual host Unity session, add
`resource:unity-editor` and `validation:unity-required`. Those exact same
Unity labels apply to Codex. If Unity cannot yet run safely, distinguish a
code/planning task (no Unity resource required) from a real scene-rendering
task; do not label an unexecuted render successful.

The host checks `RPGK_REKINDLE_UNITY_READY` for **both** providers and uses a
shared exclusive Unity lock. Actual Unity results must pass the common host
Git handoff evidence check. There is **no** `rpgk-claude-visual-authority`
JSON requirement, no `RPGK_CLAUDE_VISUAL_*` or screenshot-consent switch,
and no special approved manifest contract for Claude. Separate ownership,
licensing and production-scene constraints come from the same Rekindle
`AGENTS.md` and asset-boundary policy for both providers.

No paid Unity asset source should be checked into the GitHub PR inadvertently.
A screenshot/capture does not itself constitute final art approval. The
Supervisor still requires independent review and human signoff before merge.

### Historical experimental utilities

The early `claude-visual-policy.py`, `claude-visual-host.py`, and
`windows/run-rekindle-visual.ps1` scripts remain in the repository only for
legacy experimental fixtures. **The autonomous Claude dispatcher no longer
requires or calls them.** The normal shared Unity host infrastructure is the
supported path going forward.
