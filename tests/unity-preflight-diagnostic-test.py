#!/usr/bin/env python3
"""Preflight diagnostic contract: latest run, request matching, sanitized errors."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rpgk_preflight_diagnostic", root / "scripts/unity-preflight-diagnostic.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as temp:
    base = Path(temp)
    response = base / "Logs/SymphonyUnity/.broker/responses"
    response.mkdir(parents=True)
    now = time.time()
    payload = {
        "requestId": "GH-264-editmode-20261014T032002Z-1930923-11943",
        "operation": "editmode",
        "status": "failed", "exitCode": 1,
        "stderr": "Progress update failed (phase=staging_assets; path=\\\\wsl.localhost\\Ubuntu\\home\\dex\\secret.json): The request is not supported. token ghp_abc123ABC",
    }
    (response / "current.json").write_text(json.dumps(payload))
    result = module.describe(base, now - 20, "editmode")
    assert "staging_assets" in result and "request is not supported" in result
    assert payload["requestId"] in result
    assert "dex" not in result and "ghp_abc" not in result
    assert module.describe(base, now + 60, "editmode") == ""
    assert module.describe(base, now - 20, "playmode") == ""
    (response / "bad.json").write_text("{bad-json")
    assert "staging_assets" in module.describe(base, now - 20, "editmode")
guard = (root / "scripts/unity-resource-guard.sh").read_text()
assert 'SMOKE_DIAGNOSIS="$(python3 ' in guard
assert '${SMOKE_DIAGNOSIS}' in guard
print("unity-preflight-diagnostic-test: PASS")
