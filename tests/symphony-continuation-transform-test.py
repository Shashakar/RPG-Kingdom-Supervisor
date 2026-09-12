#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TRANSFORM = ROOT / "scripts" / "patch-symphony-continuation-policy.py"

fixture = '''defmodule SymphonyElixir.AgentRunner do
  defp run_codex_turns(workspace, issue, codex_update_recipient, opts, worker_host) do
    clear_usage_limit_marker(workspace)
    max_turns = Keyword.get(opts, :max_turns, Config.settings!().agent.max_turns)
  end

  defp do_run_codex_turns(app_session, workspace, issue, codex_update_recipient, opts, issue_state_fetcher, turn_number, max_turns) do
    case continue_with_issue?(issue, issue_state_fetcher) do
        {:continue, refreshed_issue} when turn_number < max_turns ->
          Logger.info("Continuing agent run for #{issue_context(refreshed_issue)} after normal turn completion turn=#{turn_number}/#{max_turns}")

          do_run_codex_turns(
            app_session,
            workspace,
            refreshed_issue,
            codex_update_recipient,
            opts,
            issue_state_fetcher,
            turn_number + 1,
            max_turns
          )
    end
  end

  defp build_turn_prompt(issue, opts, 1, _max_turns), do: PromptBuilder.build_prompt(issue, opts)
end
'''

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    path = root / "lib/symphony_elixir/agent_runner.ex"
    path.parent.mkdir(parents=True)
    path.write_text(fixture, encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(TRANSFORM), str(root)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    text = path.read_text(encoding="utf-8")
    assert "reset_continuation_policy!(workspace)" in text
    assert "continuation_policy(workspace, refreshed_issue, turn_number, max_turns)" in text
    assert "Continuation policy stopped automatic turn" in text
    assert "continuation-policy.py" in text
    assert '{output, 20} -> {:stop, String.trim(output)}' in text

    # Transform must be idempotent on the generated local compatibility branch.
    proc2 = subprocess.run(
        [sys.executable, str(TRANSFORM), str(root)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc2.returncode == 0, proc2.stderr

print("symphony-continuation-transform-test: PASS")
