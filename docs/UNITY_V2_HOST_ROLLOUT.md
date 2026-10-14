# Unity V2 trusted host integration: staged rollout plan

**Status:** preparation only. The live GMKTec WSL instance has NOT been changed. Do not merge or deploy this branch as a complete solution.

## Verified local starting point (from operator audit)

- Symphony checkout: `/home/dex/src/openai-symphony/elixir`, branch `rpgk/named-permissions`, commit `be94279`.
- Supervisor checkout: `/home/dex/src/RPG-Kingdom-Supervisor`, branch `unity-real-worker`, commit `4c5ede1`.
- Existing Supervisor and diagnostics systemd services run as `dex`.
- `AgentRunner.run_codex_turns` holds an `AppServer.start_session` and `stop_session` lifetime under an Elixir `try/after`.
- Local `AppServer` uses `bash -lc "exec ..."` and stores port OS PID as `session.metadata.codex_app_server_pid`.
- Remote worker sessions are NOT equivalent: the local port PID can be an SSH client.
- Current state/workspace: `/home/dex/.local/state/rpg-kingdom-supervisor` and `/home/dex/code/rpg-kingdom-symphony-workspaces`.

## Security invariant (mandatory before enabling)

Use three distinct UIDs. Controller (`dex`, initially) runs trusted orchestration, registrar/issuer (`rpgk-registrar`) owns private leases and signing material, worker (`rpgk-codex`) executes untrusted Codex. Separate identities alone are **not sufficient**. Worker commands must run under the worker UID even when Symphony launches them; this requires a dedicated, auditable runner transition or confined service manager. Never give Codex sudo, unrestricted `systemd-run`, write access to the controller's checkout, private state, sockets, or signing keys. Do not make shared writable directories the trust authority.

**Critical unresolved threat:** the current trusted controller and worker are the *same process-tree launch mechanism*. Splitting UIDs needs an allowlisted launcher that can execute ONLY the configured worker command in an allowed issue workspace, under `rpgk-codex`; it must not allow arbitrary host commands as `dex` via hooks/tools. Any Codex-authorable `WORKFLOW.md` or shell scripts/hook must remain treated as untrusted content and run under the worker UID.

The registrar owns `state/workers/active`, permissions 0700, and the Unity issuer must run as the same trusted UID or use authenticated lookup. A directory owned by `dex` cannot be trusted to store registrations while Codex also runs as `dex`.

## Runtime integration checkpoints

1. **Preflight, no mutations.** Run `python3 scripts/unity-v2-host-readiness.py` on the running host *after fetching the branch in a separate checkout*. It reports identity layout and deliberately never reports strict-admission-ready without manual launch, peer socket, and Unity acceptance tests.
2. **Safely stage, not start.** Back up both repositories, systemd unit files, live state and current executable paths. Record service UIDs, filesystem modes and path ACLs; avoid capturing secrets.
3. **Identity provisioning (privileged change).** Create dedicated system identities, private directories and narrowly scoped socket groups. Keep the live service account unchanged during preflight.
4. **Worker launch separation.** Prototype in an isolated test installation, not in the live unit. Verify that `AppServer.start_session` returns a PID that belongs to the worker UID and that `/proc/<pid>/stat` start ticks match the real process. If `exec` runs a wrapper or spawns a different process, register the stable Codex process/ancestor that the issuer verifies; never register an SSH local PID as the remote worker.
5. **Symphony lifecycle hook.** Integrate `integrations/symphony/unity_worker_registration.ex` into the separate locally modified Symphony checkout, add real `AgentRunner` tests and ensure a registration failure prevents any Codex turns. Revoke from an `after` block, including when turns throw; if the agent task is killed, kernel PID and start-tick expiry must independently reject old leases.
6. **Broker/issuer integration.** Rebase or reconcile draft PR #261, its lock/authoring verification, and the execution journal on the live branch. Keep validation and authoring request IDs idempotent. Explicit trusted host preflight must be a separate path.
7. **Acceptance matrix.** Happy path + wrong Unix UID, wrong workspace, forged worker run ID, dead process, PID reuse, duplicate lease, malformed socket requests, remote worker path, missing lock, wrong lock owner, absent author grant, issuer crash/restart, Codex crash, editor timeout, power/reboot recovery, real WSL-to-Windows Unity EditMode and authoring with safe test workspace.
8. **Controlled rollout.** Explicit operator approval required to change live systemd identities. Start with worker dispatch disabled, verify services and permissions, then one test worker. Roll back using captured units and hashes if anything fails. Resume GH-264 only after review and operator sign-off.

## Immediate operator action: one read-only command

```bash
cd ~/src/RPG-Kingdom-Supervisor
printf '=== Existing UIDs ===\\n'
for u in dex rpgk-registrar rpgk-codex; do id "$u" 2>&1 || :; done
printf '\\n=== Workspace access ===\\n'
namei -l /home/dex/code/rpg-kingdom-symphony-workspaces
printf '\\n=== Service identities ===\\n'
systemctl show rpg-kingdom-supervisor.service rpg-kingdom-diagnostics.service -p Id -p User -p Group -p ActiveState
printf '\\n=== Launch configuration (redact command secrets before sharing) ===\\n'
grep -nE 'command:|worker:|ssh_hosts:|approval_policy|sandbox' WORKFLOW.md | head -60
```

No sudo, installs, restarts, account creation, or state changes in this command. Outputs establish whether separate worker UID execution can read allowed workspaces and how the Codex command is currently configured.

## Remaining items requiring actual host work

- Audit the true local `rpgk/named-permissions` checkout (we only have user-supplied excerpts, not the complete files).
- Implement UID-switching worker launch with least privilege and prove no Codex-controlled path permits running shell commands as `dex`.
- Exercise real Elixir unit/integration tests in that fork.
- Package and review systemd migration and recovery scripts.
- Make protected issuer lease read access and signing key isolation work together.
- Enable strict Unity admission only after host acceptance.

**Safety default:** absence of evidence means do not deploy. A green GitHub Actions run for the Supervisor scripts is not a substitute for security verification of the GMKTec host.
