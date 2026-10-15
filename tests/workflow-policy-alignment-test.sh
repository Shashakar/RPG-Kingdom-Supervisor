#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW="$ROOT/WORKFLOW.md"
LABELS="$ROOT/scripts/install-labels.sh"

# Assert semantic policy markers without coupling the regression to Markdown punctuation.
grep -Fq '{% elsif issue.labels contains "authoring:scene-existing-composition" %}' "$WORKFLOW"
grep -Fq 'timeout_ms: 600000' "$WORKFLOW" || {
  echo "workflow-policy-alignment: hooks.timeout_ms must allow real Unity preflight to exceed Symphony's 60s default" >&2
  exit 1
}
grep -Fq 'existing-scene-composition' "$WORKFLOW"
grep -Eq 'risk:end-to-end.*GPT-6 Sol / high reasoning' "$WORKFLOW"
grep -Eq 'risk:end-to-end.*defaults to GPT-6 Sol / high' "$LABELS"

python3 - "$WORKFLOW" <<'PYTEST'
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

text = Path(sys.argv[1]).read_text().split("---", 2)[1]
def section(name):
    return re.search(rf"(?ms)^{name}:\n(.*?)(?=^\S|\Z)", text).group(1)
hooks = section("hooks")
codex = section("codex")
hook_timeout = int(re.search(r"timeout_ms: (\d+)", hooks).group(1))
stall_timeout = int(re.search(r"stall_timeout_ms: (\d+)", codex).group(1))
assert hook_timeout == 600000
assert stall_timeout >= hook_timeout + 60000, "watchdog must outlast before_run"
assert stall_timeout <= 900000, "keep a finite bounded watchdog"
before_run = re.search(r"(?ms)^  before_run: \|\n(.*?)(?=^  \S|\Z)", hooks).group(1)
script = "\n".join(line[4:] for line in before_run.splitlines())
with tempfile.TemporaryDirectory() as temp:
    scripts = Path(temp) / "src/RPG-Kingdom-Supervisor/scripts"
    scripts.mkdir(parents=True)
    marker = Path(temp) / "unity-invoked"
    (scripts / "before-run-guard.sh").write_text('exit "${TEST_BUDGET_EXIT}"\n')
    (scripts / "unity-resource-guard.sh").write_text(f'touch "{marker}"\n')
    env = dict(os.environ, HOME=temp, TEST_BUDGET_EXIT="75")
    result = subprocess.run(["bash", "-c", script], env=env)
    assert result.returncode == 75, "preserve budget refusal"
    assert not marker.exists(), "budget refusal must prevent Unity preflight"
    env["TEST_BUDGET_EXIT"] = "0"
    result = subprocess.run(["bash", "-c", script], env=env)
    assert result.returncode == 0 and marker.exists(), "approved attempt runs preflight"
PYTEST

echo "workflow-policy-alignment: PASS"
