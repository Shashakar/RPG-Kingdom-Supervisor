# Unity trusted worker registration — staged foundation

**Status:** code and Linux unit tests only. This PR is not deployable on the currently installed WSL topology.

## Why a separate registrar is necessary

The current `rpg-kingdom-supervisor.service` runs as Unix user `dex`, and its Codex child inherits the same UID. Mode `0600` files created by `dex` are therefore readable and modifiable by Codex. An ordinary Unix socket with `SO_PEERCRED` cannot distinguish trusted Elixir orchestration code from arbitrary Codex code running under that same UID.

`scripts/unity-worker-registrar.py` and `scripts/unity_trusted_worker_registry.py` implement a **fail-closed foundation** for a three-identity configuration:

1. Dedicated registry/issuer UID owns private `state/workers/active` (mode 0700) and the Unix socket parent directory.
2. Trusted Symphony controller UID alone is authorized, based on kernel `SO_PEERCRED`, to register/revoke worker sessions. It cannot be the registry or worker UID.
3. Separate Codex worker UID owns the actual session process. The registrar validates its real kernel UID and `/proc/<pid>/stat` start ticks. A Codex worker cannot read/write registry state or connect to the registration socket.

The registration protocol accepts only a controller-supplied `issue`, canonical existing `workspace`, integer worker process PID and uniquely scoped `runId`, and checks two successive PID identities before returning. Records are created with exclusive file creation; duplicate run IDs fail closed. Revocation must name the original process start ticks. Unknown, stale and cross-issue requests are rejected.

## Integration boundary and remaining tasks

The installed local Symphony at `~/src/openai-symphony/elixir`, branch `rpgk/named-permissions`, commit `be94279`, is **not part of this Supervisor repository**. Its `AgentRunner.run_codex_turns` calls `AppServer.start_session` and ensures `stop_session` in an `after` block. The session contains `metadata.codex_app_server_pid`; local `exec` usually replaces the Bash PID, but the configured Codex command must be checked for a wrapper.

**Do not call the registrar from today's same-UID controller and claim security.** Before real integration, arrange worker execution under a segregated UID, verify workspace access and remote workers, and add controller-side registration immediately after successful session startup; revoke in `after`; ensure orchestrator task crashes cannot leave valid sessions. The registrar's PID/live check is a safety net, not a substitute for reliable revocation. Explicitly disallow remote SSH session registration using the local port PID.

The current draft V2 issuer (PR #261) independently verifies worker lease, ancestry, lock, and signing-key separation. IMPORTANT: the protected mode-0700 registration directory can only be read by the registrar UID. The V2 issuer must run under the SAME trusted UID as the registrar (with an independently protected HMAC key), or consume an authenticated read-only lease lookup API. A fourth separate issuer UID without such a bridge will fail and MUST NOT be worked around by relaxing the registry directory permissions. It must be reconciled with this three-account model. A different signed control channel may be necessary if running a separate privileged controller UID is infeasible. Host editor preflight still requires a separate authenticated route. The SQLite execution journal already merged into main must reside in protected host state for production.

## Threat-model limits

The registrar accepts a *controller-asserted worker PID*. Therefore the controller must be trusted, protected from worker-originated code injection, and verify the process it launched. A compromised controller can submit a PID belonging to any matching worker UID. PID checks prevent obvious impersonation and reuse, but do not establish launch provenance on their own.

A compromised account capable of modifying the registrar socket directory, configuration, trusted controller code or private state invalidates the boundary. The service deliberately refuses same-UID deployment. This implementation does **not** alter systemd units, start services, create Unix users, update the live WSL tree or touch GH-264.

## Deployment acceptance

- Test a real local Codex process running with an isolated worker UID, and verify PID/start identity survives the full session.
- Exercise registration/revocation via the Unix socket with a separate controller UID; attempts from worker UID must fail.
- Verify unique active lease and downstream issuer operation/worker-lock admission.
- Verify worker crash, server restart, PID reuse, SSH remote worker fail-closed, and revocation race.
- Run WSL -> Windows Unity end-to-end only after review. Preserve the installed `unity-real-worker` checkout and the currently running services until acceptance.

## Staged Symphony controller lifecycle module (not installed)

`integrations/symphony/unity_worker_registration.ex` is an Elixir module prepared for the **separate** locally modified Symphony checkout; `scripts/unity-worker-registry-client.py` is the registration client. They are staged and not automatically loaded into the running Elixir application. The Elixir module explicitly rejects SSH-remote worker sessions and fails closed if the registration socket or client path is missing.

After the three distinct Unix identities and their protected socket access have been established, a reviewed change to local `AgentRunner.run_codex_turns` can wrap the successful `AppServer.start_session` branch:

```elixir
with {:ok, session} <- AppServer.start_session(workspace, worker_host: worker_host) do
  try do
    with {:ok, registration} <-
           SymphonyElixir.UnityWorkerRegistration.register(
             session, issue.identifier, workspace
           ) do
      try do
        do_run_codex_turns(
          session, workspace, issue, codex_update_recipient, opts,
          issue_state_fetcher, 1, max_turns
        )
      after
        case SymphonyElixir.UnityWorkerRegistration.revoke(registration) do
          :ok -> :ok
          {:error, reason} -> Logger.error("Unity worker revocation failed: #{inspect(reason)}")
        end
      end
    end
  after
    AppServer.stop_session(session)
  end
end
```

**Do not copy or activate this snippet under the current same-UID service topology.** The live controller and Codex processes both run as `dex`; registrar admission will correctly reject them. This snippet is an integration illustration, not a verified patch against `rpgk/named-permissions` or its Elixir tests. Even after port-PID registration, the local Codex command must be verified not to spawn a wrapper, the worker process must have a separate UID, and the controller's crash paths require review. Failure to revoke is not permission to blindly restart a mutation: live kernel PID/start ticks remain mandatory at issuance.
