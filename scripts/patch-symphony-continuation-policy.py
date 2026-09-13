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
        '''    prompt = build_turn_prompt(issue, opts, turn_number, max_turns)

    with {:ok, turn_session} <-
           AppServer.run_turn(
''',
        '''    prompt = build_turn_prompt(issue, opts, turn_number, max_turns)

    with :ok <- start_turn_telemetry(workspace, issue, turn_number, max_turns),
         {:ok, turn_session} <-
           AppServer.run_turn(
''',
        "turn telemetry start",
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
              with :ok <- finish_turn_telemetry(workspace, refreshed_issue, turn_number, "continue", reason) do
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
              end

            {:stop, reason} ->
              with :ok <- finish_turn_telemetry(workspace, refreshed_issue, turn_number, "continuation-budget-stop", reason) do
                Logger.warning(
                  "Continuation policy stopped automatic turn for #{issue_context(refreshed_issue)} turn=#{turn_number}/#{max_turns} policy=#{inspect(reason)}"
                )

                :ok
              end

            {:error, reason} ->
              case finish_turn_telemetry(
                     workspace,
                     refreshed_issue,
                     turn_number,
                     "continuation-policy-error",
                     inspect(reason)
                   ) do
                :ok -> {:error, reason}
                {:error, telemetry_reason} -> {:error, telemetry_reason}
              end
          end
''',
        "normal continuation branch",
    )

    replace_once(
        agent_runner,
        '''        {:continue, refreshed_issue} ->
          Logger.info("Reached agent.max_turns for #{issue_context(refreshed_issue)} with issue still active; returning control to orchestrator")

          :ok

        {:done, _refreshed_issue} ->
          :ok

        {:error, reason} ->
          {:error, reason}
''',
        '''        {:continue, refreshed_issue} ->
          with :ok <-
                 finish_turn_telemetry(
                   workspace,
                   refreshed_issue,
                   turn_number,
                   "hard-turn-cap",
                   "agent.max_turns reached while the issue remained active and routable"
                 ) do
            Logger.info("Reached agent.max_turns for #{issue_context(refreshed_issue)} with issue still active; returning control to orchestrator")
            :ok
          end

        {:done, refreshed_issue} ->
          finish_turn_telemetry(
            workspace,
            refreshed_issue,
            turn_number,
            "tracker-complete",
            "tracker item is no longer active/routable after the completed turn"
          )

        {:error, reason} ->
          case finish_turn_telemetry(
                 workspace,
                 issue,
                 turn_number,
                 "tracker-refresh-error",
                 inspect(reason)
               ) do
            :ok -> {:error, reason}
            {:error, telemetry_reason} -> {:error, telemetry_reason}
          end
''',
        "terminal turn outcomes",
    )

    replace_once(
        agent_runner,
        '''  defp build_turn_prompt(issue, opts, 1, _max_turns), do: PromptBuilder.build_prompt(issue, opts)
''',
        '''  defp supervisor_script(name) do
    supervisor_root =
      System.get_env("RPGK_SUPERVISOR_ROOT") || Path.expand("~/src/RPG-Kingdom-Supervisor")

    Path.join([supervisor_root, "scripts", name])
  end

  defp continuation_policy_script, do: supervisor_script("continuation-policy.py")
  defp turn_telemetry_script, do: supervisor_script("turn-telemetry.py")

  defp rpgk_supervisor_workspace?(workspace) do
    Regex.match?(~r/^GH-\\d+$/, Path.basename(workspace))
  end

  defp reset_continuation_policy!(workspace) do
    if rpgk_supervisor_workspace?(workspace) do
      script = continuation_policy_script()

      case System.cmd("python3", [script, "reset", "--workspace", workspace],
             cd: workspace,
             stderr_to_stdout: true
           ) do
        {_output, 0} -> :ok
        {output, status} -> raise "continuation policy reset failed status=#{status}: #{String.trim(output)}"
      end
    else
      :ok
    end
  end

  defp start_turn_telemetry(workspace, issue, turn_number, max_turns) do
    if rpgk_supervisor_workspace?(workspace) do
      args = [
        turn_telemetry_script(),
        "start",
        "--workspace",
        workspace,
        "--issue",
        issue.identifier,
        "--turn",
        Integer.to_string(turn_number),
        "--max-turns",
        Integer.to_string(max_turns),
        "--labels-json",
        Jason.encode!(issue.labels || [])
      ]

      case System.cmd("python3", args, cd: workspace, stderr_to_stdout: true) do
        {_output, 0} -> :ok
        {output, status} -> {:error, {:turn_telemetry_start_failed, status, String.trim(output)}}
      end
    else
      :ok
    end
  end

  defp finish_turn_telemetry(workspace, issue, turn_number, decision, reason) do
    if rpgk_supervisor_workspace?(workspace) do
      args = [
        turn_telemetry_script(),
        "finish",
        "--workspace",
        workspace,
        "--issue",
        issue.identifier,
        "--turn",
        Integer.to_string(turn_number),
        "--decision",
        decision,
        "--reason",
        reason
      ]

      case System.cmd("python3", args, cd: workspace, stderr_to_stdout: true) do
        {_output, 0} -> :ok
        {output, status} -> {:error, {:turn_telemetry_finish_failed, status, String.trim(output)}}
      end
    else
      :ok
    end
  end

  defp continuation_policy(workspace, issue, turn_number, max_turns) do
    if rpgk_supervisor_workspace?(workspace) do
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
    else
      {:continue, "Supervisor continuation policy not applicable outside GH workspaces"}
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
