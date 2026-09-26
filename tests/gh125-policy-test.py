#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

dashboard = (ROOT / "scripts/supervisor_dashboard.py").read_text()
html = (ROOT / "scripts/supervisor_dashboard.html").read_text()
actions = (ROOT / "scripts/operator_actions.py").read_text()
continuation = (ROOT / "scripts/continuation-policy-base.py").read_text()
author = (ROOT / "scripts/windows/run-unity-authoring.ps1").read_text()

assert "/api/operator/rearm" in dashboard and "/api/operator/merge" in dashboard
assert "X-RPGK-Action-Token" in dashboard
assert "Needs Manual Validation" in html
assert "Rearm below reserve" in html and "Merge PR" in html
assert '"symphony:halted" not in labels' in actions
assert '"symphony:human-review" not in labels' in actions
assert "--match-head-commit" in actions
assert "operator-approved below-reserve continuation" in continuation
assert "Assets/RPGKingdom/Generated/AgentDerivatives/" in author
assert "derivative source" in author and "DerivativeCopyBackAssets" in author
assert "changed-assets evidence must exactly match the authorized scene, navigation assets, and exact derivative outputs" in author
print("gh125-policy-test: PASS")
