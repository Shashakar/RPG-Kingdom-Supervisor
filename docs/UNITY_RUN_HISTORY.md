# Unity Run History and Failure Diagnostics

## Purpose

The Supervisor dashboard keeps Unity validation history visible after the host broker returns to `ready` or restarts. It is a read-only observability feature: it does not dispatch work, retry Unity, mutate issues, or merge pull requests.

The execution boundary remains unchanged. `scripts/unity-runner.sh` submits typed requests, `scripts/unity-host-broker.py` owns host execution, and the Windows runner produces Unity artifacts. The dashboard reconstructs historical run records from those existing durable files rather than introducing a second execution database or patching Symphony.

## Durable history inputs

Each worker request keeps an immutable copy at:

```text
GH-N/Logs/SymphonyUnity/.broker/history/requests/<request-id>.json
```

The transient broker request is still consumed normally. The durable copy preserves:

- request ID;
- operation (`health`, `editmode`, or `playmode`);
- requested test filter;
- request timestamp.

The history reader combines that envelope with:

- broker acknowledgement and response JSON;
- the broker's current `status.json` for an active run and elapsed time;
- `summary.json` when Unity reached result collection;
- `results.xml` when the Unity Test Framework produced results;
- `Editor.log` when available;
- the artifact directory printed by the host runner when Unity fails before a summary exists.

Because broker responses and request-history files live in the issue workspace, completed runs remain inspectable across broker process restarts. Destroying the issue workspace also destroys that workspace-local history.

## Derived run record

`scripts/unity_run_history.py` exposes a structured record containing, where available:

- request ID and `GH-N` workspace;
- operation and requested test filter;
- requested/accepted/start/completion timestamps and duration;
- current/final status and exit code;
- artifact directory;
- paths for `Editor.log`, `results.xml`, `summary.json`, broker request, and broker response;
- summary/test counts;
- failed test names, assertion messages, and stack traces;
- concise derived diagnosis and retryability classification;
- bounded relevant error excerpts;
- raw broker request/ack/response data for deeper inspection.

The active broker operation is represented as a `running` record with its current elapsed seconds.

## Diagnosis priority

The history reader chooses the strongest evidence available rather than treating every non-zero Unity exit as a generic test failure.

Priority is:

1. broker/host states such as `HostBusy`, `TimedOut`, `StaleRequest`, or `BrokerStopped`;
2. C# compiler errors found in `Editor.log`;
3. failed test cases and assertions from `results.xml`;
4. Unity licensing, activation, project-load, batchmode, fatal, or crash evidence from `Editor.log`;
5. zero-test matches;
6. Unity failure before test results were produced;
7. generic Unity failure;
8. successful validation.

Compiler diagnostics extract the source path, line, column, compiler code, and message. A compile failure with no `results.xml` is therefore reported as a compilation failure with `testsStarted=false`, not as a normal PlayMode/EditMode test failure.

Infrastructure classifications that are inherently transient, such as host timeout/busy/broker-stop conditions, are marked retryable. Code/test failures are not.

## Dashboard

Start the existing diagnostics entrypoint:

```bash
bash scripts/serve-diagnostics.sh
```

Then open:

```text
http://127.0.0.1:8765
```

The dashboard remains bound to localhost and read-only. In addition to the existing issue/Symphony/workspace status, it shows:

- the active Unity operation and elapsed time;
- recent historical Unity runs for the selected issue;
- filters for operation and status;
- concise failure diagnosis directly in the run table;
- an expandable run detail view with failed tests, compiler/runtime excerpts, summary data, broker request/response data, and artifact paths.

The dashboard exposes machine-readable read-only endpoints that Codex/operator tooling can query without rediscovering files manually:

```text
GET /api/unity/runs?issue=GH-98&operation=playmode&status=failed&limit=100
GET /api/unity/run/<request-id>
```

The existing issue endpoint remains:

```text
GET /api/issue/<number>
```

## Retention

The dashboard defaults to showing completed runs from the last 30 days. Change the visibility window with:

```bash
export RPGK_UNITY_HISTORY_RETENTION_DAYS=14
```

This is a read/display retention window, not destructive cleanup. Historical broker/artifact files remain in their issue workspace until normal workspace cleanup removes them.

The history reader uses `RPGK_WORKSPACE_ROOT` when configured, falls back to `RPGK_SYMPHONY_WORKSPACE_ROOT`, and otherwise uses the standard Symphony workspace root.

## Regression coverage

`tests/unity-run-history-test.py` uses filesystem fixtures and never launches Unity. It covers:

- successful validation;
- normal Unity test failure with assertion details;
- compilation failure before tests/results are produced;
- host timeout/infrastructure failure;
- active-run elapsed-time reporting;
- issue/operation/status filtering;
- dashboard history/detail route presence.

This keeps the feature inside the Supervisor's rule that orchestration behavior must be testable without running the Unity production scene.
