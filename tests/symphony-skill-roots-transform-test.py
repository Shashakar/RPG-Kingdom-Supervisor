#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TRANSFORM = ROOT / "scripts" / "patch-symphony-skill-roots.py"

app_server_fixture = '''defmodule SymphonyElixir.Codex.AppServer do
  @initialize_id 1
  @thread_start_id 2
  @turn_start_id 3

  defp send_initialize(port) do
    :ok
  end

  defp session_policies(workspace, nil) do
    Config.codex_runtime_settings(workspace)
  end

  defp do_start_session(port, workspace, session_policies, dynamic_tool_binding) do
    case send_initialize(port) do
      :ok -> start_thread(port, workspace, session_policies, dynamic_tool_binding)
      {:error, reason} -> {:error, reason}
    end
  end
end
'''

agent_runner_fixture = '''defmodule SymphonyElixir.AgentRunner do
  defp build_turn_prompt(issue, opts, 1, _max_turns), do: PromptBuilder.build_prompt(issue, opts)
end
'''

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    app_server = root / "lib/symphony_elixir/codex/app_server.ex"
    agent_runner = root / "lib/symphony_elixir/agent_runner.ex"
    app_server.parent.mkdir(parents=True)
    agent_runner.parent.mkdir(parents=True, exist_ok=True)
    app_server.write_text(app_server_fixture, encoding="utf-8")
    agent_runner.write_text(agent_runner_fixture, encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(TRANSFORM), str(root)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    app_text = app_server.read_text(encoding="utf-8")
    runner_text = agent_runner.read_text(encoding="utf-8")
    assert '"method" => "skills/extraRoots/set"' in app_text
    assert 'RPGK_SUPERVISOR_SKILLS_ROOT' in app_text
    assert 'configure_supervisor_skill_roots(port, workspace)' in app_text
    assert 'Regex.match?(~r/^GH-\\d+$/, Path.basename(workspace))' in app_text
    assert 'else\n      :ok\n    end' in app_text
    assert '$rpgk-investigate-bug' in runner_text
    assert 'risk:investigative' in runner_text

    proc2 = subprocess.run(
        [sys.executable, str(TRANSFORM), str(root)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc2.returncode == 0, proc2.stderr

print("symphony-skill-roots-transform-test: PASS")
