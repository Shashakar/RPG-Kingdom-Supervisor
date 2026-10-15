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

## Router findings from the repository (must verify against installed branch)

The configured Codex command invokes `scripts/codex-app-server-router.sh`, rather than a direct `codex app-server`. The repository version of this router sources routing/concurrency policies, fetches issue labels, generates continuation context, writes capability and telemetry state, acquires `flock`-based issue and concurrency slots, executes model compatibility/usage probes, and **finally `exec codex ... app-server`**. Therefore the same OS PID should ultimately become Codex, but it starts as a trusted router executing host-state mutations and privileged credential-bearing scripts.

**Security implication:** running the entire router under the untrusted worker UID would lose access to protected state and GitHub orchestration credentials; running it as the trusted `dex` controller would allow early router/hook execution before worker isolation. The account switch belongs **after all trusted routing, locking and telemetry preparations, and before any arbitrary Codex-controlled command or plugin is executed**. Before using a UID switch, audit each router dependency and both stdout/stdio and inherited file descriptors. In particular the existing inherited `flock` FDs, environment variables, state-owned files, Codex tool capabilities, authentication material, and stderr process substitutions need explicit separation rules. No worker should inherit sensitive FDs, or controller secrets.

There is a second integration issue: the staged `UnityWorkerRegistration.register` is called after `AppServer.start_session` has completed its Codex handshake, so the issuer must NOT allow Unity tool calls until the authenticated registration completes; tests should prove no tool/turn begins before registration. A misconfigured client must halt the attempt before any mutation.

**Host evidence:** the live account check on October 14 showed only `dex` (UID 1000); `rpgk-registrar` and `rpgk-codex` do not exist. `/home/dex` is mode 0750, preventing an isolated worker from traversing it without carefully scoped ACLs or a worker-accessible relocated workspace. Live system services `rpg-kingdom-supervisor` and `rpg-kingdom-diagnostics` both run as `dex`. The live `WORKFLOW.md` sets `command: bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/codex-app-server-router.sh"`. No account creation, chmod, or service changes have been performed.

## Fixed-command isolated worker launcher prototype

`scripts/unity-isolated-codex-launch.py` is now available as a **staged prototype only**. It validates an existing canonical `GH-<number>` workspace immediately below the allowed root; requires root invocation, a distinct fixed `rpgk-codex` UID, and a root-owned/non-writable executable at `/usr/local/libexec/rpgk/codex-worker`; constructs a minimal environment that omits controller tokens and overrides worker HOME; closes inherited file descriptors; drops supplementary groups, GID and UID; and finally replaces the process with Codex.

**DO NOT install it as setuid Python, configure unrestricted sudo, or make the router call it now.** It is deliberately not wired to the router, and the command-line workspace root and argv still require immutable, operator-pinned authority at the privilege-escalation boundary. A malicious worker must not be able to invoke the launcher as root or substitute arbitrary workspace, executable, preload/library, config or arguments. The deployed privilege helper must pin workspace root (not read it from worker arguments), verify the caller's trusted UID, validate the allowed Codex argument grammar, and independently validate the executable and its ancestors. The current source does not yet enforce all these invariants. The existing `dex` router holds file descriptors 8/9 for `flock`; closing them before worker exec releases locks unless trusted orchestration retains separate lock handles. The router must first be refactored so trusted controller process(es) hold locks for worker lifetime, then test concurrency and clean worker exits.

The installed `codex` binary is under `/home/dex/.local/share/mise/installs/node/24.20.0/bin/codex` rather than the pinned root-owned prototype path; it must not be copied blindly. A genuinely isolated worker also needs its own credential provisioning and restrictive access to game workspaces, without access to `/home/dex/.config` or trusted Supervisor code/state. The real Node/shebang interpreter and dependency chain must be audited.

## Launcher argument policy progress

The prototype now pins worker account (`rpgk-codex`), trusted controller account (`dex`), worker executable (`/usr/local/libexec/rpgk/codex-worker`) and workspace root (`/srv/rpgk/workspaces`) in code, and invokes `validate_codex_args` before dropping privilege. Its restrictive allowlist presently permits only the model, effort, and shell-inheritance configuration switches. **Current production router arguments include `RPGK_CODEX_PERMISSION_ARGS` and MCP capability arguments that this allowlist intentionally rejects.** This is correct fail-closed behavior for the prototype, but it means it cannot run current production workloads. Those options require an explicitly enumerated, reviewed option grammar before integration. The worker-user `codex` home and binary interpreter dependencies also remain to be provisioned safely.

Also, the root launcher is currently a Python script that trusts root invocation and does not authenticate a non-root caller; it must be invoked only through an immutable, narrowly scoped privileged service or IPC endpoint. Do not configure a blanket sudo rule for this script or accept a caller-selected root process invocation.

## Trusted concurrency locks: staged integration (not deployed)

`scripts/codex-trusted-lock-keeper.py` demonstrates a controller-owned holder for the existing mutation/review slot lock and per-issue lock. It opens the **same paths in the same order** as `codex-concurrency-policy.sh` and starts a subprocess with `close_fds=True`, so the worker itself does not receive file descriptors for those lock files. The controller waits for the subprocess before releasing locks. This helps avoid granting the isolated worker access to the protected lock directory.

**Important:** This prototype is not wired to the live router. The current router still directly holds FDs 8 and 9 and `exec`s Codex; blindly stacking the lock keeper around the existing router would deadlock on the same locks. The worker launcher and lock keeper cannot simply be composed as-is: the real implementation must move locking to a trusted session owner and remove the old router locking section in a coordinated change. The existing `System.cmd`/Erlang Port interface requires a single stdin/stdout-preserving Codex process; a parent lock keeper must transparently propagate signals and preserve the JSON-RPC stream. The current keeper only launches and waits, does not implement signal forwarding or process-group cleanup, and is **not an approved host deployment mechanism**.

The safe next implementation must establish a clear lifecycle owner for both locks and the registered worker PID, ensure termination kills or positively invalidates descendants, and prove it with a concurrent two-worker test plus crash/restart tests. Neither mode-0700 lease access nor the issuer/worker-UID separation may be relaxed as a workaround.

## Keeper PID versus Codex PID

The new `scripts/unity_worker_process_identity.py` validates a local child process from kernel `/proc` records, including the worker's real/effective/saved/filesystem UID, immutable start ticks and immediate parent PID. It expressly rejects substituting the lock-keeper PID for the Codex worker PID. Tests cover wrong parent, wrong UID and mixed credentials.

This is a prerequisite, not a completed handoff: the current lock keeper does **not** publish an authenticated PID/start-time record, and the existing Symphony `Port.os_pid` still refers to the keeper in a wrapped launch. Before using the helper in production, an issuer- or registrar-owned side channel must establish the actual worker PID and associate it with one controller-authorized session and workspace, resist PID reuse, and invalidate it when the process or controller is gone. Neither an untrusted JSON file nor a worker-supplied PID may be accepted as the identity source. Remote SSH workers remain excluded.

The current process group cleanup test is necessary but insufficient proof of a clean cgroup after the foreground worker exits. Production readiness still requires a real systemd scope/cgroup test.

## Staged registrar peer-ancestry handoff

The new registrar `registerChild` action obtains the controller-client PID from Linux `SO_PEERCRED`, checks that the requested Codex worker PID is its sibling under a live controller-UID keeper, and revalidates worker UID and immutable kernel process identity before writing the protected lease. The staged Python client now uses `registerChild`.

**Not yet a deployable protocol:** an older `register` action still exists for compatibility with the foundation tests; production must remove/disable it, or the stricter action can be bypassed. The trusted controller/keeper must actually spawn the registrar client as a sibling of the isolated Codex worker; this has not been wired into the local Symphony checkout. The child must also demonstrably be Codex itself after the UID transition rather than a wrapper. Tests must cover client exit races, missing keeper, PID reuse, mismatched workspace, and revocation on all exit paths. No local WSL acceptance has been run.

## Registration handoff status

The old registrar `register` operation is no longer accepted. A sibling-identity-checked `registerChild` operation remains. The lock keeper contains an experimental `registered_worker_session` helper, but its normal `run` path does not call it. Registration currently occurs after a child can start, so the sequence must not be enabled until a trusted startup gate and process supervision are added. Host services remain unchanged.

## Admission gate code handoff (October 14)

The prototype acknowledgement checker `scripts/unity_v2_admission_gate.py` is committed and invoked by the keeper's staged `registered_worker_session`. It only writes a gate release byte when the registrar response contains a matching worker PID, run ID and decimal kernel start ticks. `start_gated_session` creates a keeper-owned pipe, substitutes exactly one `{RPGK_GATE_FD}` token within the **trusted, operator-constructed launch command**, and gives only the read end to the child. The keeper retains the write end. The negative and positive acknowledgement cases are in `tests/unity-v2-admission-gate-test.py`.

This is staging code, **not activated by the live router or by `run`**. The keeper still lacks verified failure-path cleanup across all subprocess/time-out cases, there is no cgroup isolation for descendants, and the pinned launcher requires privileged host provisioning. The issuer and Symphony lifecycle are not wired. A gate receipt alone is not proof the worker executable is Codex after the identity transition. Do not deploy until an actual integration acceptance harness proves no pre-admission side effects, reliable startup/stop/revoke and correct app-server stdio transport.

## Unprivileged keeper integration harness

`tests/unity-v2-keeper-integration-test.py` runs the staged `start_gated_session` with a simulated restricted worker and a simulated registrar client. It checks successful registration allows the child past the gate and triggers revocation, while registrar denial prevents the child from running. It is included in `tests/run.sh`. This harness does not establish real Linux identity separation or registrar authentication, and does not test a privileged launcher, Symphony, Unity, or detached child termination. It must not be interpreted as production acceptance. The trusted keeper path is still opt-in and not used by the live router.
