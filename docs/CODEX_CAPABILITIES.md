# Route-Aware Codex Capabilities

Supervisor #34 extends model routing into a controlled **context/tool routing** layer. The objective is to reduce repeated repository exploration without making third-party tools authoritative, broadening worker permissions, or loading every integration into every worker.

This first slice intentionally supports only:

- a Supervisor-owned `rpgk-investigate-bug` skill for `risk:investigative` issues;
- optional local Graphify MCP access for Terra, Sol, and Astra routes when a fresh graph is already available;
- durable telemetry describing what the worker was offered/selected.

Context7, Ponytail experiments, additional first-party skills, actual MCP-use aggregation, and comparative efficiency reporting remain later #34 work.

## Authority

RPG Kingdom's checked-in `AGENTS.md` and repository documentation remain authoritative. Skills and MCP results supplement that source material; they do not replace it.

Workers must not install Graphify, rewrite `AGENTS.md`, mutate user-level Codex configuration, or repair host capability setup from inside an issue workspace.

## First-party investigative skill

The skill is stored at:

```text
skills/rpgk-investigate-bug/SKILL.md
```

The Symphony compatibility layer registers the Supervisor `skills/` directory at App Server startup with `skills/extraRoots/set`.

For a `risk:investigative` issue, the first-turn prompt is prefixed with:

```text
$rpgk-investigate-bug
```

That marker asks Codex to load the detailed skill on demand. The full skill body is not copied into `WORKFLOW.md`, and the marker is not repeated on continuation turns. An explicit `model:terra` override on a non-investigative issue does **not** select the investigative skill; task semantics come from the risk label, not the model name.

The skill tells the worker to:

1. reproduce the narrow failure first;
2. prefer graph queries for the initial structural pass when Graphify is available;
3. stop broad exploration after evidence identifies the owning boundary;
4. implement the smallest coherent fix;
5. validate narrowly before broadening;
6. preserve a precise continuation point if Supervisor stops the worker.

## Graphify policy

Graphify is an optional, read-only repository-structure provider. Supervisor does not run Graphify's assistant installer and does not let Graphify alter RPG Kingdom instructions.

### Selection

Default mode is `auto`:

| Route | Graphify requested by default |
| --- | --- |
| Luna | no |
| Terra | yes |
| Sol | yes |
| Astra | yes |

A requested Graphify capability is enabled only when all of the following are true:

- the configured MCP executable exists;
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

## Codex wiring

When Graphify is enabled for a worker, the router adds bounded per-process Codex configuration for a server named `rpgk_graphify`:

```text
mcp_servers.rpgk_graphify.command=<absolute graphify-mcp executable>
mcp_servers.rpgk_graphify.args=[<absolute graph.json>]
mcp_servers.rpgk_graphify.startup_timeout_sec=10
mcp_servers.rpgk_graphify.tool_timeout_sec=30
```

This is passed as process CLI configuration. Supervisor does not rewrite `~/.codex/config.toml` and does not add repository `.mcp.json` policy files.

The Graphify MCP is treated as read-only structural evidence. Its graph is built from the canonical repository and therefore does not automatically contain uncommitted changes in the current Symphony workspace. Workers must confirm edit decisions against current workspace source.

## Telemetry

Before a worker starts, capability selection is persisted under:

```text
~/.local/state/rpg-kingdom-supervisor/capabilities/GH-N.json
```

The same snapshot is attached to the durable worker telemetry record and a `worker_capabilities_selected` event is appended.

The record includes:

- route;
- risk labels relevant to selection;
- selected first-party skills;
- Graphify requested/available/enabled state;
- explicit unavailable reason;
- graph path and freshness evidence;
- MCP server name;
- whether the integration is read-only.

This first slice records **selection/availability**, not authoritative tool-call counts. Actual MCP calls must be derived from Codex rollout/App Server events in a later #34 slice before the dashboard claims an integration was actually used.

## Safety boundaries

- No generic host shell is exposed through this feature.
- No new credentials are passed to Codex.
- Existing named-permission, runtime workspace root, Git handoff, Unity broker, turn-budget, and no-auto-merge boundaries remain unchanged.
- Graphify absence in `auto` mode never escalates the model or creates another worker lifetime.
- Third-party installer-generated `AGENTS.md`, hooks, or global Codex configuration are not part of the Supervisor contract.
- Ponytail remains disabled in this slice.

## Measuring whether this helps

Do not call this an efficiency win merely because Graphify is available. Compare completed workloads using the existing worker telemetry and the future #34 actual-use fields:

- total and cached input tokens;
- output/reasoning tokens;
- turns;
- runtime;
- continuations/rearms;
- completion/handoff outcome;
- Graphify selected vs actually used;
- first-party skill selected.

GH-108 is the motivating outlier, not sufficient evidence by itself that Graphify reduces cost.
