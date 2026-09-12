#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text and old not in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one {label} anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch-symphony-continuation-policy.py <symphony-elixir-root>", file=sys.stderr)
        return 64

    root = Path(sys.argv[1]).resolve()
    agent_runner = root / "lib/symphony_elixir/agent_runner.ex"
    if not agent_runner.is_file():
        raise RuntimeError(f"expected Symphony source file is missing: {agent_runner}")

    replace_once(
        agent_runner,
        '''  defp run_codex_turns(workspace, issue, codex_update_recipient, opts, worker_host) do
    clear_usage_limit_marker(workspace)
    max_turns = Keyword.get(opts, :max_turns, Config.settings!().agent.max_turns)
''',
        '''  defp run_codex_turns(workspace, issue, codex_update_recipient, opts, worker_host) do
    clear_usage_limit_marker(workspace)
    reset_continuation_policy!(workspace)
    max_turns = Keyword.get(opts, :max_turns, Config.settings!().agent.max_turns)
''',
        "continuation policy reset",
    )

    replace_once(
        agent_runner,
        '''        {:continue, refreshed_issue} when turn_number < max_turns ->
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
''',
        '''        {:continue, refreshed_issue} when turn_number < max_turns ->
          case continuation_policy(workspace, refreshed_issue, turn_number, max_turns) do
            {:continue, reason} ->
              Logger.info(
                "Continuing agent run for #{issue_context(refreshed_issue)} after continuation policy approval turn=#{turn_number}/#{max_turns} policy=#{inspect(reason)}"
              )

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

            {:stop, reason} ->
              Logger.warning(
                "Continuation policy stopped automatic turn for #{issue_context(refreshed_issue)} turn=#{turn_number}/#{max_turns} policy=#{inspect(reason)}"
              )

              :ok

            {:error, reason} ->
              {:error, reason}
          end
''',
        "normal continuation branch",
    )

    replace_once(
        agent_runner,
        '''  defp build_turn_prompt(issue, opts, 1, _max_turns), do: PromptBuilder.build_prompt(issue, opts)
''',
        '''  defp continuation_policy_script do
    supervisor_root =
      System.get_env("RPGK_SUPERVISOR_ROOT") || Path.expand("~/src/RPG-Kingdom-Supervisor")

    Path.join(supervisor_root, "scripts/continuation-policy.py")
  end

  defp reset_continuation_policy!(workspace) do
    script = continuation_policy_script()

    case System.cmd("python3", [script, "reset", "--workspace", workspace],
           cd: workspace,
           stderr_to_stdout: true
         ) do
      {_output, 0} -> :ok
      {output, status} -> raise "continuation policy reset failed status=#{status}: #{String.trim(output)}"
    end
  end

  defp continuation_policy(workspace, issue, turn_number, max_turns) do
    script = continuation_policy_script()
    labels_json = Jason.encode!(issue.labels || [])

    args = [
      script,
      "evaluate",
      "--workspace",
      workspace,
      "--issue",
      issue.identifier,
      "--turn",
      Integer.to_string(turn_number),
      "--max-turns",
      Integer.to_string(max_turns),
      "--labels-json",
      labels_json
    ]

    case System.cmd("python3", args, cd: workspace, stderr_to_stdout: true) do
      {output, 0} -> {:continue, String.trim(output)}
      {output, 20} -> {:stop, String.trim(output)}
      {output, status} -> {:error, {:continuation_policy_failed, status, String.trim(output)}}
    end
  end

  defp build_turn_prompt(issue, opts, 1, _max_turns), do: PromptBuilder.build_prompt(issue, opts)
''',
        "continuation helper insertion",
    )

    print("RPG Kingdom Symphony continuation-policy transform: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
