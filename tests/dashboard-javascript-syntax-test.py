#!/usr/bin/env python3
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "scripts" / "supervisor_dashboard.html").read_text(encoding="utf-8")
scripts = re.findall(r"<script>([\s\S]*?)</script>", HTML)
assert scripts, "dashboard contains no inline script"

for index, script in enumerate(scripts):
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
        handle.write(script)
        path = handle.name
    result = subprocess.run(["node", "--check", path], text=True, capture_output=True, check=False)
    assert result.returncode == 0, f"dashboard script {index} has invalid JavaScript:\n{result.stderr}"

print("dashboard-javascript-syntax-test: PASS")
