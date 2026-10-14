# Durable Unity execution journal

Unity operations are potentially non-idempotent: applying a scene transaction twice may mutate a prefab or scene twice. Both host brokers therefore reserve each validated non-health request ID in `$RPGK_SUPERVISOR_STATE_ROOT/unity-execution-journal.sqlite3` **before** starting the WSL/Windows adapter.

## Behavior

- Each `(canonical workspace, operation, request ID)` has one durable reservation.
- A duplicate receives `DuplicateRequest` (exit 93) before Unity starts, even after the client deleted the previous response.
- A journal write/validation failure returns `ExecutionJournalUnavailable` (exit 70) and refuses execution.
- Health checks do not reserve IDs. Existing short authoring IDs remain compatible; explicit `GH-N-...` IDs must name the matching workspace issue.
- The journal is shared by validation and authoring brokers and survives broker restarts.

## Important limits

This guarantees **at-most-once admission to start** for one request ID, not exactly-once application of Unity changes. A broker crash after reservation may leave the Unity child running or may have prevented launch. Do not work around an ambiguous outcome by generating a fresh ID and replaying the same mutation. Preserve the journal and reconcile the editor and Git workspace first.

The journal is Supervisor-owned trusted state. Production file and parent-directory permissions must prevent worker processes from deleting, replacing or editing it; worker-accessible state roots are not a sufficient security boundary. Keep the database in state backups and do not clear it as routine cleanup. The journal is not an authentication mechanism, and new IDs do not identify logically duplicate operations.

The host running `unity-real-worker` still requires reconciliation and real Windows Unity acceptance before applying these changes to the installed service. A GitHub merge does not deploy or restart the host.
