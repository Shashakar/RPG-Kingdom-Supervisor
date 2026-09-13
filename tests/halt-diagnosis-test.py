#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


status = load("worker_status_tested", ROOT / "scripts" / "worker-status.py")
diagnosis = load("halt_diagnosis_tested", ROOT / "scripts" / "halt-diagnosis.py")

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    state_root = root / "state"
    os.environ["RPGK_SUPERVISOR_STATE_ROOT"] = str(state_root)
    workspace = root / "GH-111"
    workspace.mkdir()
    os.system(f"git -C {workspace} init -q")
    os.system(f"git -C {workspace} config user.email test@example.com")
    os.system(f"git -C {workspace} config user.name Test")
    (workspace / "seed.txt").write_text("seed\n", encoding="utf-8")
    os.system(f"git -C {workspace} add seed.txt && git -C {workspace} commit -qm seed")

    # First lifetime has no prior marker, so a status recorded now is fresh for boundary=none.
    status.write_status(
        workspace,
        state="blocked",
        classification="manual_action_required",
        summary="The remaining correct fix requires production-scene composition.",
        remaining=["Bind Rustfang forced target to PlayerCharacter.BasicAITarget"],
        blocked_by="Repository rules prohibit model-authored production scene YAML.",
        manual_action_required=True,
        recommended_next_action="Open VerticalSlice in Unity and author the target binding, then rearm for validation.",
        validation_runs=["run-red"],
    )
    assert (workspace / status.STATUS_NAME).exists()
    exclude = os.popen(f"git -C {workspace} check-ignore {status.STATUS_NAME}").read().strip()
    assert exclude == status.STATUS_NAME

    (workspace / ".symphony-turn-history.jsonl").write_text(json.dumps({
        "turn": 4,
        "hardMaxTurns": 4,
        "decision": "stop",
        "unityAfter": {"runId": "run-red", "result": "Failed(Child)"},
    }) + "\n", encoding="utf-8")
    (workspace / "changed.txt").write_text("dirty\n", encoding="utf-8")

    payload = diagnosis.collect(workspace, 111, "none", "worker_lifetime_ended", state_root=state_root)
    assert payload["supervisor"]["classification"] == "hard_turn_ceiling"
    assert payload["taskStatus"]["classification"] == "manual_action_required"
    assert payload["taskStatus"]["manualActionRequired"] is True
    assert payload["latestUnity"]["runId"] == "run-red"
    assert "hard_turn_ceiling" in payload["markdown"]
    assert "manual_action_required" in payload["markdown"]
    assert "Rustfang" in payload["markdown"]
    persisted = json.loads((state_root / "halt-diagnostics" / "GH-111.json").read_text(encoding="utf-8"))
    assert persisted["supervisor"]["classification"] == "hard_turn_ceiling"

    # Once a new attempt marker exists, the old status must not be reused as if it were fresh.
    marker = workspace / status.ATTEMPT_MARKER
    marker.write_text("prior attempt\n", encoding="utf-8")
    new_boundary = str(marker.stat().st_mtime_ns)
    stale_payload = diagnosis.collect(workspace, 111, new_boundary, "worker_lifetime_ended", state_root=state_root)
    assert stale_payload["taskStatus"] is None
    assert "prior worker lifetime" in stale_payload["taskStatusUnavailableReason"]
    assert "Manual action required:** unknown" in stale_payload["markdown"]

    # A process/lifetime failure with no turn/status evidence must not invent a semantic blocker.
    (workspace / status.STATUS_NAME).unlink()
    (workspace / ".symphony-turn-history.jsonl").unlink()
    unknown = diagnosis.collect(workspace, 111, new_boundary, "worker_lifetime_ended", state_root=state_root)
    assert unknown["supervisor"]["classification"] == "worker_lifetime_ended"
    assert unknown["taskStatus"] is None
    assert "do not assume" in unknown["markdown"]

    # Specialized stop types retain their deterministic classification.
    (workspace / diagnosis.USAGE_MARKER).write_text(json.dumps({"reason": "usage_limit_exceeded", "message": "quota exhausted"}), encoding="utf-8")
    quota = diagnosis.collect(workspace, 111, new_boundary, "usage_limit_exceeded", state_root=state_root)
    assert quota["supervisor"]["classification"] == "usage_limit_exceeded"
    (workspace / diagnosis.CONTINUATION_MARKER).write_text(json.dumps({"decision": "stop", "reason": "second consecutive host-invisible turn"}), encoding="utf-8")
    continuation = diagnosis.collect(workspace, 111, new_boundary, "continuation_policy", state_root=state_root)
    assert continuation["supervisor"]["classification"] == "continuation_policy"
    assert "host-invisible" in continuation["supervisor"]["reason"]

print("halt-diagnosis-test: PASS")
