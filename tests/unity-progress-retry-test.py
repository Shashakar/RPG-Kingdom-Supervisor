#!/usr/bin/env python3
"""Static safety assertions; Windows integration exercised by optional PS test."""
from pathlib import Path
script = (Path(__file__).resolve().parents[1] / "scripts/windows/run-unity-tests.ps1").read_text()
assert '[System.IO.File]::Replace($tempPath, $targetPath, $backupPath)' in script
assert '[System.IO.File]::Move($tempPath, $targetPath)' in script
assert '$backupPath = ' in script
assert '[System.IO.File]::Delete($backupPath)' in script
assert '$attempt -le 6' in script
assert 'catch [System.IO.IOException]' in script
assert 'catch [System.UnauthorizedAccessException]' in script
assert 'Start-Sleep -Milliseconds (50 * $attempt)' in script
assert 'Move-Item -LiteralPath $tempPath -Destination $ProgressPath -Force' not in script
assert 'request=$RequestId; phase=$Phase; path=$ProgressPath' in script
print("unity-progress-retry-test: PASS")
