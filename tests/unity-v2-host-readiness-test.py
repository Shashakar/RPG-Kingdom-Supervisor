#!/usr/bin/env python3
"""Host readiness reports fail closed and never mutate host state."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
spec=importlib.util.spec_from_file_location("host_readiness",Path(__file__).resolve().parents[1]/"scripts"/"unity-v2-host-readiness.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with TemporaryDirectory() as temp:
    state=Path(temp)/"state"
    workspace=Path(temp)/"ws"
    workspace.mkdir()
    report=module.report("unlikely-rpgk-controller-test","unlikely-rpgk-registrar-test","unlikely-rpgk-worker-test",state,workspace)
    assert report["safe_for_strict_admission"] is False
    assert report["checks"]["distinct_existing_uids"] is False
    assert report["checks"]["unity_wsl_windows_acceptance_verified"] is False
    assert not (state/"workers"/"active").exists(), "readiness created state"
    with patch.object(module,"identity",side_effect=[
        {"exists":True,"uid":111},{"exists":True,"uid":111},{"exists":True,"uid":333}
    ]):
        duplicate=module.report("one","two","three",state,workspace)
        assert duplicate["checks"]["distinct_existing_uids"] is False
print("unity-v2-host-readiness-test: PASS")
