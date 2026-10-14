#!/usr/bin/env python3
"""Exercise the host's immutable progress selection without running Unity."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

root = Path(__file__).resolve().parents[1]
writer = (root / "scripts/windows/run-unity-tests.ps1").read_text()
assert '$isWslUnc = $ProgressPath -match' in writer
assert '"$ProgressPath.seq.' in writer
assert '[System.IO.File]::Move($tempPath, $targetPath)' in writer
assert '[System.IO.File]::Replace($tempPath, $targetPath, $backupPath)' in writer

spec = importlib.util.spec_from_file_location("unity_host_broker", root / "scripts/unity-host-broker.py")
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "request.json"
    active = SimpleNamespace(
        spec=SimpleNamespace(request_id="GH-264-editmode-test"),
        progress_path=path, last_progress_sequence=-1,
    )
    def write(suffix, request, sequence):
        Path(str(path) + suffix).write_text(
            json.dumps({"requestId": request, "sequence": sequence, "phase": "staging_assets"}),
            encoding="utf-8",
        )
    write(".seq.0000000001", "GH-264-editmode-test", 1)
    write(".seq.0000000002", "wrong-request", 2)
    Path(str(path) + ".seq.0000000003").write_text("{corrupt", encoding="utf-8")
    assert module.read_progress(active)["sequence"] == 1
    assert module.refresh_progress(active)
    assert not module.refresh_progress(active)
    write(".seq.0000000004", "GH-264-editmode-test", 4)
    assert module.refresh_progress(active)
    assert active.last_progress_sequence == 4
    # Legacy/native progress at an older sequence cannot override the latest.
    path.write_text(json.dumps({"requestId": active.spec.request_id, "sequence": 2}))
    assert not module.refresh_progress(active)
    active.stdout_handle = SimpleNamespace(close=lambda: None)
    active.stderr_handle = SimpleNamespace(close=lambda: None)
    module.close_operation(active)
    assert not list(path.parent.glob(path.name + ".seq.*"))
print("unity-unc-progress-protocol-test: PASS")
