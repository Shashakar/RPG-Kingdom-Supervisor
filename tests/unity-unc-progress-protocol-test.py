#!/usr/bin/env python3
"""Bounded structural regression for write-once WSL UNC progress protocol."""
from pathlib import Path
root = Path(__file__).resolve().parents[1]
writer = (root / "scripts/windows/run-unity-tests.ps1").read_text()
broker = (root / "scripts/unity-host-broker.py").read_text()
assert '$isWslUnc = $ProgressPath -match' in writer
assert '[System.IO.File]::Move($tempPath, $targetPath)' in writer
assert '[System.IO.File]::Replace($tempPath, $targetPath, $backupPath)' in writer
assert '"$ProgressPath.seq.' in writer
assert 'glob(active.progress_path.name + ".seq.*")' in broker
assert 'payload.get("requestId") != active.spec.request_id' in broker
assert 'sequence > active.last_progress_sequence' in broker
print("unity-unc-progress-protocol-test: PASS")
