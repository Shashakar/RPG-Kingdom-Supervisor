# Route-Aware Codex Capabilities

Supervisor #34 extends model routing into a controlled **context/tool routing** layer. The objective is to reduce repeated repository exploration without making third-party tools authoritative, broadening worker permissions, or loading every integration into every worker.

The implemented capability surface now includes:

- Supervisor-owned progressive-disclosure skills for `risk:mechanical`, `risk:investigative`, `risk:architecture`, and `risk:end-to-end` workers;
- no extra procedural skill for ordinary `risk:normal` work, keeping the default instruction surface small;
- optional local Graphify MCP access for Sol and Astra routes when a fresh graph is already available;
- optional remote Context7 MCP access for Sol and Astra routes for current external-library documentation;
- durable per-turn telemetry that distinguishes a capability being selected from it actually being used;
- comparative usage diagnostics for selected+used, selected+unused, selected+unknown, and not-selected worker groups.

Ponytail remains deliberately disabled because the current Codex plugin surface does not provide the worker-level positive allowlisting boundary Supervisor requires. The useful low-risk minimal-change behavior is implemented as a Supervisor-owned skill instead.

## Authority

RPG Kingdom's checked-in `AGENTS.md` and repository documentation remain authoritative. Skills and MCP results supplement that source material; they do not replace it.

Workers must not install Graphify or Context7, rewrite `AGENTS.md`, mutate user-level Codex configuration, or repair host capability setup from inside an issue workspace.

## First-party skill policy

First-party procedural guidance lives under `skills/` and is selected by `scripts/first-party-skill-policy.py`. The Codex capability router first selects the approved MCP/tool surface, then the first-party skill policy augments the same capability snapshot before the durable worker record is created.

The Symphony compatibility layer registers the Supervisor `skills/` directory with App Server through `skills/extraRoots/set`. On the **first** model turn only, it prefixes the matching skill marker so Codex loads the detailed body through progressive disclosure rather than copying that procedure into every worker prompt.

Selection is deterministic from the existing risk label:

| Risk class | First-party skill |
| --- | --- |
| `risk:mechanical` | `rpgk-mechanical-change` |
| `risk:normal` | none |
| `risk:investigative` | `rpgk-investigate-bug` |
| `risk:architecture` | `rpgk-architecture-change` |
| `risk:end-to-end` | `rpgk-end-to-end-change` |

An explicit model override does not change the skill. Task semantics come from the risk label; model/effort routing and procedural skill routing remain separate decisions. Conflicting risk labels fail before a worker is allowed to start.

### `rpgk-mechanical-change`

This is the controlled replacement for the useful part of the proposed Ponytail experiment. It instructs low-risk workers to:

1. confirm the already-known owning boundary rather than rediscovering the architecture;
2. make the smallest **complete** change, not merely the smallest line count;
3. avoid opportunistic refactors, cleanup, abstraction, and dependency work;
4. preserve system ownership and stop/reclassify if the task unexpectedly requires architecture work;
5. validate exactly the changed behavior; and
6. stop once acceptance is satisfied rather than spending remaining turns searching for more improvements.

Because this is our own skill, Supervisor can select it by risk class without inheriting unrelated user plugin state or giving a third-party methodology ownership of branching, tests, review, handoff, or merge policy.

### `rpgk-investigate-bug`

Investigative workers are told to reproduce narrowly, use Graphify for the initial structural pass when available, stop broad exploration once evidence identifies the owning boundary, distinguish production defects from stale fixtures/infrastructure, implement the smallest coherent fix, and validate from narrow to broad.

### `rpgk-architecture-change`

Architecture workers must establish current ownership/contracts first, state the boundary being changed, keep designs bounded, migrate consumers through public contracts, update save/event/system documentation when required, and prove the new contract with deterministic tests before broad integration validation.

### `rpgk-end-to-end-change`

End-to-end workers map the complete path before editing, preserve each system's ownership, implement in dependency order, use Context7 only for genuine external-library questions, validate each owner before the full path, avoid unrelated cleanup, and account explicitly for any remaining human-authored scene/asset step.

The skill files remain supplements to `AGENTS.md`; they cannot weaken or replace repository architecture/safety rules.

## Graphify policy

Graphify is an optional, read-only repository-structure provider. Supervisor does not run Graphify's assistant installer and does not let Graphify alter RPG Kingdom instructions.

### Selection

Default mode is `auto`:

| Route | Graphify requested by default |
| --- | --- |
| Luna | no |
| Sol | yes |
| Astra | yes |

A requested Graphify capability is enabled only when all of the following are true:

- the configured MCP executable exists;
- the configured Graphify version matches the evaluated version;
- the configured graph file exists;
- the graph is fresh relative to the canonical repository HEAD, unless stale use was explicitly opted into.

In `auto` mode, unavailable/stale Graphify fails soft: the worker starts without it and the reason is retained in capability telemetry. The investigative skill tells the worker to fall back to targeted repository search rather than spend model time trying to repair host tooling.

Set `RPGK_GRAPHIFY_ENABLED=on` when an operator deliberately wants Graphify to be mandatory; missing or stale setup then fails before the Codex worker starts. Set it to `off` to disable the experiment globally.

### Evaluated local layout

By default Supervisor expects:

```text
canonical repository: ~/src/RPG-Kingdom
graph:                ~/src/RPG-Kingdom/graphify-out/graph.json
MCP executable:       graphify-mcp
```

Overrides:

```text
RPGK_GRAPHIFY_ENABLED=auto|on|off
RPGK_GRAPHIFY_ALLOW_STALE=on|off
RPGK_GRAPHIFY_REPO_ROOT=/absolute/path/to/RPG-Kingdom
RPGK_GRAPHIFY_GRAPH=/absolute/path/to/graph.json
RPGK_GRAPHIFY_MCP_COMMAND=/absolute/path/to/graphify-mcp
RPGK_GRAPHIFY_EXPECTED_VERSION=0.9.58
```

Do not enable stale graphs merely to satisfy capability checks. The graph is structural acceleration, not authority.

### Bootstrap on the host

Graphify is not installed automatically by Supervisor. An operator may install the local MCP-capable package outside worker execution, for example with `uv`:

```bash
uv tool install 'graphifyy[mcp]'
```

Build/update the graph against the canonical RPG Kingdom checkout rather than an ephemeral GH workspace:

```bash
cd ~/src/RPG-Kingdom
git switch main
git pull --ff-only
graphify .
```

Then verify Supervisor's view without launching a model turn:

```bash
cd ~/src/RPG-Kingdom-Supervisor
python3 scripts/codex-capability-policy.py status --pretty
```

A deep route should report Graphify `available: true` and `enabled: true` in `auto` mode when the graph is current.

Refresh the graph after meaningful canonical-repository updates. Supervisor intentionally refuses to treat a graph whose modification time predates canonical `HEAD` as fresh.

### Codex wiring

When Graphify is enabled for a worker, the router adds bounded per-process Codex configuration for a server named `rpgk_graphify`:

```text
mcp_servers.rpgk_graphify.command=<absolute graphify-mcp executable>
mcp_servers.rpgk_graphify.args=[<absolute graph.json>]
mcp_servers.rpgk_graphify.startup_timeout_sec=10
mcp_servers.rpgk_graphify.tool_timeout_sec=30
```

This is passed as process CLI configuration. Supervisor does not rewrite `~/.codex/config.toml` and does not add repository `.mcp.json` policy files.

The Graphify MCP is treated as read-only structural evidence. Its graph is built from the canonical repository and therefore does not automatically contain uncommitted changes in the current Symphony workspace. Workers must confirm edit decisions against current workspace source.

## Context7 policy

Context7 is an optional, read-only provider for **external dependency/framework/library documentation**. It is not a repository-search replacement and should not be used when RPG Kingdom source or checked-in docs already answer the question.

Default `auto` routing matches the deep routes where current external API knowledge is more likely to justify the extra tool surface:

| Route | Context7 offered by default |
| --- | --- |
| Luna | no |
| Sol | yes |
| Astra | yes |

Configuration:

```text
RPGK_CONTEXT7_ENABLED=auto|on|off
RPGK_CONTEXT7_URL=https://mcp.context7.com/mcp
```

`on` explicitly offers Context7 even to Luna routes; `off` disables it globally. Supervisor requires an absolute HTTPS URL and passes the endpoint only as per-process Codex MCP configuration:

```text
mcp_servers.rpgk_context7.url="https://mcp.context7.com/mcp"
mcp_servers.rpgk_context7.startup_timeout_sec=10
mcp_servers.rpgk_context7.tool_timeout_sec=30
```

Supervisor does **not** install an npm package in the worker, does not write user-level MCP configuration, and does not inject a Context7 API key or authorization header. The default remote endpoint can therefore be used without placing a provider secret in RPG Kingdom or worker-visible instructions. If authenticated Context7 becomes necessary later, add it only through a reviewed secret-safe host surface rather than embedding a key in route configuration.

Context7 is intentionally fail-soft in normal `auto` use: its selection means the documentation tool is offered, not that the worker must call it. Actual calls are measured separately.

## Per-turn actual-use telemetry

Capability selection alone is not evidence of efficiency. The #60 turn boundary now also examines the uniquely attributable Codex rollout and retains MCP calls for that turn when the rollout exposes them.

For each completed turn, `worker_turn_completed` telemetry may contain:

```text
mcpUsage.status
mcpUsage.totalCalls
mcpUsage.byCapability.<name>.enabled
mcpUsage.byCapability.<name>.used
mcpUsage.byCapability.<name>.calls
mcpUsage.byCapability.<name>.tools
```

The collector understands current `mcp_tool_call` records, older begin/end records, and compatible `mcp__<server>__<tool>` function-call encodings. Calls are deduplicated by call ID so a begin/end pair is not counted twice. When a pre-turn rollout sample is unavailable, only the first turn may use the attributable cumulative rollout as its turn-local basis; later turns report actual-use telemetry as unavailable rather than guessing.

The raw call arguments/results are not copied into Supervisor telemetry. Only bounded server/tool identity and counts are retained.

## Comparative diagnostics

`scripts/supervisor_usage_analysis.py` joins durable worker lifetimes with retained `worker_turn_completed` events. The existing dashboard endpoint is exportable directly:

```text
GET /api/usage-analysis
```

For each known capability it provides worker groups:

- `selected_used` — capability was enabled and at least one attributable call was retained;
- `selected_unused` — capability was enabled, actual-use telemetry was available, and no call occurred;
- `selected_unknown` — capability was enabled but retained rollout evidence cannot prove use/non-use;
- `not_selected` — capability was not enabled for the worker.

Each group uses the same worker metrics as the existing usage analysis: median tokens, duration, authoritative quota deltas where available, outcome, and MCP-call samples. `bySkillBundle` separately compares first-party skill selection.

This lets us compare Graphify used vs not used and Context7 used vs not used without treating mere availability as success. Do not claim an efficiency win from a single worker; compare enough completed issues within similar risk/task classes to reduce selection bias.

## Ponytail experiment status

Ponytail remains **disabled**. This is deliberate rather than unfinished installation work.

The current Codex plugin surface supports plugin discovery/installation and global disabling, but the evaluated client does not yet give Supervisor a sufficiently clean positive per-worker selection boundary for one installed plugin without potentially inheriting unrelated user plugin state. That conflicts with #34's explicit allowlist requirement. The upstream tracking discussion is `openai/codex#30967`.

Do not work around that limitation by installing Ponytail into RPG Kingdom, rewriting `AGENTS.md`, or globally enabling user plugin state. Revisit the experiment when Codex can select an installed plugin/capability by logical identity per worker/thread, or when a separate reviewed process-local Ponytail surface can provide equivalent isolation.

The intended low-risk behavior is already covered by `rpgk-mechanical-change`, which is narrower and fully controlled by Supervisor. If Ponytail later becomes safely isolatable, it should be evaluated against that skill rather than assumed to be an improvement.

## Adding or removing an approved integration

Third-party MCP/tool integrations belong in `scripts/codex-capability-policy.py`, not in RPG Kingdom repository instructions or user-global Codex state.

A reviewed MCP/tool addition should:

1. define a stable Supervisor capability name and MCP/server identity;
2. document its authority and read/write boundary;
3. define deterministic route selection and an `auto|on|off` kill switch where appropriate;
4. pass only process-local Codex configuration;
5. fail clearly when explicitly required but invalid/unavailable;
6. appear in the persisted capability snapshot;
7. be attributable through per-turn usage telemetry where the rollout supports it;
8. add deterministic policy and telemetry tests;
9. preserve named permissions, credential scrubbing, Git/Unity brokers, bounded turns, review, and no-auto-merge guarantees.

First-party procedural skills belong under `skills/` and in `scripts/first-party-skill-policy.py`. A new automatically selected skill also requires the matching first-turn marker in the Symphony skill-root transform and deterministic tests proving that the policy and marker use the same risk/task boundary. Core invariants remain in RPG Kingdom `AGENTS.md`; a skill is only appropriate for procedure that can safely be loaded on demand.

Removal is the inverse: disable it by policy first, remove its process-local configuration or marker, retain historical telemetry compatibility, then remove any host bootstrap only after no active worker depends on it.

## Safety boundaries

- No generic host shell is exposed through this feature.
- No new credentials are passed to Codex.
- Existing named-permission, runtime workspace root, credential scrubbing, Git handoff, Unity broker, turn-budget, review/rework, and no-auto-merge boundaries remain unchanged.
- Graphify absence in `auto` mode never escalates the model or creates another worker lifetime.
- Context7 selection never requires a model call and does not force the worker to query external docs.
- First-party skills add no host permissions and cannot override checked-in RPG Kingdom instructions.
- Third-party installer-generated `AGENTS.md`, hooks, or global Codex configuration are not part of the Supervisor contract.
- Ponytail remains disabled until its per-worker selection boundary is safe enough to preserve the allowlist.

## Measuring whether this helps

Do not call an integration an efficiency win merely because it is available. Compare completed workloads using the retained selection and actual-use evidence:

- total and cached input tokens;
- output/reasoning tokens;
- turns and continuations;
- runtime;
- authoritative quota deltas when available;
- completion/handoff outcome;
- Graphify selected vs actually used;
- Context7 selected vs actually used;
- first-party skill bundle;
- MCP call count/tools used.

GH-108 is the motivating outlier, not sufficient evidence by itself that any capability reduces cost.
