#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSFORM = ROOT / "scripts" / "patch-symphony-usage-limit.py"


def load_transform():
    spec = importlib.util.spec_from_file_location("usage_limit_transform", TRANSFORM)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_transform()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        app_server = root / "lib/symphony_elixir/codex/app_server.ex"
        agent_runner = root / "lib/symphony_elixir/agent_runner.ex"
        app_server.parent.mkdir(parents=True)
        agent_runner.parent.mkdir(parents=True, exist_ok=True)

        app_server.write_text(
            '''defmodule SymphonyElixir.Codex.AppServer do
  defp handle_incoming(port, on_message, data, timeout_ms, tool_executor, auto_approve_requests) do
    payload_string = to_string(data)

    case Jason.decode(payload_string) do
      {:ok, %{"method" => "turn/completed"} = payload} ->
        emit_turn_event(on_message, :turn_completed, payload, payload_string, port, payload)
        {:ok, :turn_completed}
    end
  end

  defp emit_turn_event(on_message, event, payload, payload_string, port, payload_details) do
    :ok
  end
end
''',
            encoding="utf-8",
        )

        agent_runner.write_text(
            '''defmodule SymphonyElixir.AgentRunner do
  defp run_codex_turns(workspace, issue, codex_update_recipient, opts, worker_host) do
    max_turns = Keyword.get(opts, :max_turns, Config.settings!().agent.max_turns)
  end

  defp do_run_codex_turns(app_session, workspace, issue, codex_update_recipient, opts, issue_state_fetcher, turn_number, max_turns) do
    prompt = build_turn_prompt(issue, opts, turn_number, max_turns)

    with {:ok, turn_session} <-
           AppServer.run_turn(app_session, prompt, issue, on_message: codex_message_handler(codex_update_recipient, issue)) do
      case continue_with_issue?(issue, issue_state_fetcher) do
        {:error, reason} ->
          {:error, reason}
      end
    end
  end

  defp build_turn_prompt(issue, opts, 1, _max_turns), do: PromptBuilder.build_prompt(issue, opts)
end
''',
            encoding="utf-8",
        )

        old_argv = module.sys.argv
        try:
            module.sys.argv = [str(TRANSFORM), str(root)]
            assert module.main() == 0
        finally:
            module.sys.argv = old_argv

        app_text = app_server.read_text(encoding="utf-8")
        runner_text = agent_runner.read_text(encoding="utf-8")

        assert 'case usage_limit_details(payload) do' in app_text
        assert '{:error, {:usage_limit_exceeded, details}}' in app_text
        assert 'Map.get(value, "codex_error_info") == "usage_limit_exceeded"' in app_text
        assert 'retry_at_from_message' in app_text

        assert 'clear_usage_limit_marker(workspace)' in runner_text
        assert 'write_usage_limit_marker(workspace, details)' in runner_text
        assert 'ending worker lifetime without continuation turns' in runner_text
        assert '.symphony-usage-limit.json' in runner_text

        # The transform is deliberately idempotent so the generated compatibility
        # branch can be rebuilt/verified repeatedly from the evaluated Symphony pin.
        old_argv = module.sys.argv
        try:
            module.sys.argv = [str(TRANSFORM), str(root)]
            assert module.main() == 0
        finally:
            module.sys.argv = old_argv

    print("symphony-usage-limit-transform-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
