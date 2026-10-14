#!/usr/bin/env python3
import importlib.util
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=root/"scripts"/"unity-v2-router-audit.py"
spec=importlib.util.spec_from_file_location("router_audit",p)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
router=(root/"scripts"/"codex-app-server-router.sh").read_text()
result=m.inspect(router)
assert not result["missing"],result["missing"]
assert result["requires_separated_uid_launcher"]
assert result["requires_file_descriptor_audit"]
assert not result["safe_to_enable_strict_admission"]
assert "final_exec_codex" in result["observations"]
assert m.inspect("exec codex app-server")["missing"]
print("unity-v2-router-audit-test: PASS")
