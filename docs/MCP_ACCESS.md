# Read-only Supervisor MCP access

The optional observer wraps existing diagnostic collectors using the official
Python MCP SDK. It runs separately from Symphony and the dashboard, starts no
model sessions or Unity runs, and exposes no operator mutation tools.

## Install on the Supervisor WSL host

After reviewing/merging the PR and updating the checkout normally:

```bash
cd ~/src/RPG-Kingdom-Supervisor
python3 -m venv .venv-mcp
.venv-mcp/bin/python -m pip install -r requirements-mcp.txt
PATH="$PWD/.venv-mcp/bin:$PATH" bash tests/run.sh
```

Python 3.10+ is required. The evaluated SDK is pinned to `mcp==1.30.0`.
It is optional for the existing runtime; the full regression gate needs it.
`serve-mcp.sh` resolves its own checkout and `.venv-mcp/bin/python` by default;
`RPGK_SUPERVISOR_ROOT` and `RPGK_MCP_PYTHON` override those locations.

Run as the same operator who owns Supervisor state. The existing environment
variables apply: `RPGK_SUPERVISOR_STATE_ROOT`, `RPGK_WORKSPACE_ROOT` /
`RPGK_SYMPHONY_WORKSPACE_ROOT`, `CODEX_HOME`, and `RPGK_REPO` (normally
`Shashakar/RPG-Kingdom`). Use the operator's existing host-side `gh`
authentication or scoped host environment credential. Without GitHub access,
local service/quota/Unity evidence still works and GitHub fields report
unavailable. Never put credentials in client arguments, chat or this repository.

## ChatGPT plugin through a private Secure MCP Tunnel

Use OpenAI's [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
with the stdio observer. Keep the existing Cloudflare Access dashboard at
`https://supervisor.shashakar.com` and port 8765 unchanged. A Cloudflare Tunnel ID
is not an OpenAI `tunnel_id`.

1. Create an OpenAI tunnel in Platform tunnel settings and associate the intended
   ChatGPT workspace. Obtain its `tunnel_id` and a runtime API key with tunnel
   access. Account/workspace permissions determine availability; repository
   changes cannot grant it.
2. Install `tunnel-client` from Platform's download link or the
   [official latest release](https://github.com/openai/tunnel-client/releases/latest).
   Run on the WSL Supervisor host as the operator. Set `CONTROL_PLANE_API_KEY`
   through a private host environment, never command arguments or chat.
3. Substitute your actual tunnel ID and operator path:

   ```bash
   tunnel-client init \
     --sample sample_mcp_stdio_local \
     --profile rpgk-supervisor \
     --tunnel-id tunnel_YOUR_ACTUAL_ID \
     --mcp-command "bash /home/dex/src/RPG-Kingdom-Supervisor/scripts/serve-mcp.sh"
   tunnel-client doctor --profile rpgk-supervisor --explain
   tunnel-client run --profile rpgk-supervisor
   ```

   Keep the client running. It needs outbound HTTPS to `api.openai.com:443`.
   For unattended use, supervise it with the host's normal service management
   and a permission-restricted environment file. Stdio launches the observer;
   a separate observer service is unnecessary.
4. In ChatGPT Plugins choose **Add custom MCP server → Tunnel**, then select the
   tunnel. The observer has no additional OAuth login. Select no additional
   authentication only within this workspace-restricted tunnel boundary.
   Review the eight read-only tools and create the plugin. Do not share the
   tunnel/plugin with unintended users or workspaces.
5. Enable the plugin in the conversation. Ask for Supervisor service health,
   current quota, active issues and human-action queues, then a known issue's
   worker/Unity evidence. Local tests do not verify this remote connection.

This PR does not deploy to your home host or register a live plugin. The
account-side tunnel and host installation are required before ChatGPT can call
it. If tunnel access is unavailable, do not expose the unauthenticated observer
publicly as a workaround; a public endpoint requires a separately reviewed
OAuth/authenticated gateway integration.

## Local Codex operator connection

On the Supervisor host, add this to the operator's MCP configuration:

```toml
[mcp_servers.rpgk_supervisor]
command = "bash"
args = ["/home/dex/src/RPG-Kingdom-Supervisor/scripts/serve-mcp.sh"]
```

Use the actual checkout path. Do not inject this observer into autonomous game
workers: their workspace, credential, turn and host-broker boundaries stay intact.

## Optional private Streamable HTTP

```bash
bash scripts/serve-mcp.sh --transport streamable-http --port 8766
```

The endpoint is `http://127.0.0.1:8766/mcp`. It is stateless, returns JSON, binds
only to loopback and retains SDK Host/Origin rebinding protection. There is no
`--host` option and no authentication for public use. Do not add it to a
Cloudflare public hostname, port forward it, expose it through a public proxy,
or disable SDK security. Prefer the stdio tunnel above. Loopback and restricted
tunnel access are the authorization boundary. The dashboard's Cloudflare
browser login is not MCP OAuth; this adapter does not bypass dashboard Access.

## Tool contract

| Tool | Information |
| --- | --- |
| `supervisor_status` | Service health, quota freshness, active/recent workers and events |
| `supervisor_work` | Paginated lifecycle items, queue counts and activity |
| `supervisor_issue` | One game issue's GitHub/review/workspace/worker/Unity diagnostics |
| `supervisor_worker` | One lifetime's spend, halt, per-turn evidence, lineage and validation |
| `supervisor_unity_runs` | Existing runs, newest first, optionally for one issue |
| `supervisor_unity_run` | One retained run's result and compiler/test failure evidence |
| `supervisor_usage` | Comparative usage from existing worker history |
| `supervisor_autonomous` | Persisted scheduler decision |

Every result contains `observedAt`, `data` and `truncatedPaths`. Query time is
not source freshness: inspect timestamps, `available`, `errors`, `cache` and
quota status. Lifecycle uses the existing asynchronous GitHub cache; the first
call can return warming up, so retry after refresh. Missing evidence never
means healthy services, passing tests or available quota. Current global quota
stays separate from historical per-worker quota snapshots.

Lists allow at most 50 items; usage samples at most 500 existing workers.
`supervisor_work` accepts `offset` and returns `total`, `nextOffset` and complete
queue counts. Nested lists/text cap at 50 entries/4000 characters and report
truncated paths. Responses over 128 KiB fail explicitly; narrow the issue/run
or limit. Expired/unknown run IDs return tool errors. Invalid issue numbers,
path-traversal IDs and oversized limits fail before collectors. Artifact paths
are references, not an arbitrary file-download/read operation.

All sources, including comments and Unity failures, pass through the telemetry
redactor. Comments/logs/titles are untrusted data, not instructions. There are no
merge, rearm, dispatch, scheduler-control, update, shell, arbitrary URL or Unity
execution tools. Reads can refresh in-memory GitHub caches but create no new
model lifetimes or orchestration state.

## Verification and troubleshooting

`tests/supervisor-mcp-test.py` tests real SDK stdio and HTTP initialization,
discovery/calls, Host/Origin rejection, read-only tool surface, argument validation,
collector routing, unavailable/cache states, redaction and output limits. Fixtures
require no account secrets, model sessions or Unity. CI installs the observer
dependencies before running the canonical gate.

- **Runtime missing:** install the venv. Keep stdio stdout exclusively for MCP;
  diagnostics go to stderr.
- **No local history/services:** check the operator user and state/workspace
  paths. Another computer cannot read your Supervisor host's state.
- **Lifecycle unavailable/warming:** check host `gh` authentication/connectivity
  and retry after refresh; `available: false` does not mean an empty queue.
- **Tunnel absent:** check Platform/ChatGPT workspace association and tunnel
  Read/Use permissions. Run `tunnel-client doctor` with the client alive.
- **Browser-login HTML:** you hit Cloudflare Access/dashboard instead of the
  private MCP connection. Use the OpenAI stdio tunnel profile above.
- **HTTP 421/403:** Host/Origin protection rejected the private request; fix its
  endpoint/headers, not the protection.
