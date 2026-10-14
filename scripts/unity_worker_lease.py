"""Shared, fail-closed optional worker-claim validation for Unity brokers.

Legacy requests without a claim remain supported until the worker client migration.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
from typing import Callable

WORKER_ID = re.compile(r"GH-[0-9]+-[A-Za-z0-9_-]{1,160}")
REJECT_STATUS = "WorkerLeaseRejected"
REJECT_EXIT = 75

def parse_claim(request: dict) -> str | None:
    claim = request.get("expectedWorkerRunId")
    required = request.get("requiresWorkerLease")
    if required is not None and not isinstance(required, bool):
        raise ValueError("requiresWorkerLease must be boolean")
    if claim is not None and (not isinstance(claim, str) or not WORKER_ID.fullmatch(claim)):
        raise ValueError("invalid expectedWorkerRunId")
    if claim is not None and required is False:
        raise ValueError("expectedWorkerRunId conflicts with requiresWorkerLease=false")
    if required is True and claim is None:
        raise ValueError("requiresWorkerLease requires expectedWorkerRunId")
    return claim

def resolve_unique_live_lease(state_root: Path, workspace: Path, alive: Callable[[int], bool]) -> tuple[str | None, int | None]:
    matches = []
    for path in sorted((state_root / "workers" / "active").glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                continue
            recorded_workspace = Path(str(record.get("workspace") or "")).expanduser().resolve()
            pid = int(record.get("pid") or 0)
            run_id = str(record.get("runId") or "").strip()
            if recorded_workspace == workspace and pid > 0 and run_id and alive(pid):
                matches.append((run_id, pid))
        except (OSError, ValueError, TypeError):
            continue
    return matches[0] if len(matches) == 1 else (None, None)

def verify_claim(claim: str | None, state_root: Path, workspace: Path, alive: Callable[[int], bool]) -> tuple[str | None, int | None, bool]:
    worker_id, pid = resolve_unique_live_lease(state_root, workspace, alive)
    return worker_id, pid, claim is not None and (pid is None or claim != worker_id)
