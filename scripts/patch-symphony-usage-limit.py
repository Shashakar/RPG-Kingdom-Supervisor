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
        print("usage: patch-symphony-usage-limit.py <symphony-elixir-root>", file=sys.stderr)
        return 64

    root = Path(sys.argv[1]).resolve()
    app_server = root / "lib/symphony_elixir/codex/app_server.ex"
    agent_runner = root / "lib/symphony_elixir/agent_runner.ex"

    for path in (app_server, agent_runner):
        if not path.is_file():
            raise RuntimeError(f"expected Symphony source file is missing: {path}")

    replace_once(
        app_server,
        '''      {:ok, %{"method" => "turn/completed"} = payload} ->
        emit_turn_event(on_message, :turn_completed, payload, payload_string, port, payload)
        {:ok, :turn_completed}
''',
        '''      {:ok, %{"method" => "turn/completed"} = payload} ->
        emit_turn_event(on_message, :turn_completed, payload, payload_string, port, payload)

        case usage_limit_details(payload) do
          nil -> {:ok, :turn_completed}
          details -> {:error, {:usage_limit_exceeded, details}}
        end
''',
        "turn/completed usage-limit handling",
    )

    replace_once(
        app_server,
        '''  defp emit_turn_event(on_message, event, payload, payload_string, port, payload_details) do
''',
        r'''  defp usage_limit_details(payload) do
    case find_usage_limit(payload) do
      nil ->
        nil

      _match ->
        message = find_usage_limit_message(payload)

        %{
          codex_error_info: "usage_limit_exceeded",
          message: message,
          retry_at: retry_at_from_message(message)
        }
    end
  end

  defp find_usage_limit(value) when is_map(value) do
    cond do
      Map.get(value, "codex_error_info") == "usage_limit_exceeded" -> value
      Map.get(value, :codex_error_info) == "usage_limit_exceeded" -> value
      true -> Enum.find_value(value, fn {_key, child} -> find_usage_limit(child) end)
    end
  end

  defp find_usage_limit(value) when is_list(value), do: Enum.find_value(value, &find_usage_limit/1)

  defp find_usage_limit(value) when is_binary(value) do
    if String.contains?(String.downcase(value), "usage_limit_exceeded"), do: value, else: nil
  end

  defp find_usage_limit(_value), do: nil

  defp find_usage_limit_message(value) when is_map(value) do
    Enum.find_value(value, fn {_key, child} -> find_usage_limit_message(child) end)
  end

  defp find_usage_limit_message(value) when is_list(value),
    do: Enum.find_value(value, &find_usage_limit_message/1)

  defp find_usage_limit_message(value) when is_binary(value) do
    lower = String.downcase(value)

    if String.contains?(lower, "usage limit") or String.contains?(lower, "try again at") do
      value
    end
  end

  defp find_usage_limit_message(_value), do: nil

  defp retry_at_from_message(message) when is_binary(message) do
    case Regex.run(~r/try again at\s+(.+?)(?:\.|$)/i, message, capture: :all_but_first) do
      [retry_at] -> String.trim(retry_at)
      _ -> nil
    end
  end

  defp retry_at_from_message(_message), do: nil

  defp emit_turn_event(on_message, event, payload, payload_string, port, payload_details) do
''',
        "usage-limit helper insertion",
    )

    replace_once(
        agent_runner,
        '''  defp run_codex_turns(workspace, issue, codex_update_recipient, opts, worker_host) do
    max_turns = Keyword.get(opts, :max_turns, Config.settings!().agent.max_turns)
''',
        '''  defp run_codex_turns(workspace, issue, codex_update_recipient, opts, worker_host) do
    clear_usage_limit_marker(workspace)
    max_turns = Keyword.get(opts, :max_turns, Config.settings!().agent.max_turns)
''',
        "usage-limit marker reset",
    )

    replace_once(
        agent_runner,
        '''        {:error, reason} ->
          {:error, reason}
      end
    end
  end

  defp build_turn_prompt(issue, opts, 1, _max_turns), do: PromptBuilder.build_prompt(issue, opts)
''',
        '''        {:error, reason} ->
          {:error, reason}
      end
    else
      {:error, {:usage_limit_exceeded, details}} ->
        write_usage_limit_marker(workspace, details)

        Logger.warning(
          "Codex usage quota exhausted for #{issue_context(issue)}; ending worker lifetime without continuation turns"
        )

        :ok

      {:error, reason} ->
        {:error, reason}
    end
  end

  defp usage_limit_marker(workspace), do: Path.join(workspace, ".symphony-usage-limit.json")

  defp clear_usage_limit_marker(workspace) do
    _ = File.rm(usage_limit_marker(workspace))
    :ok
  end

  defp write_usage_limit_marker(workspace, details) do
    payload = %{
      reason: "usage_limit_exceeded",
      observed_at: DateTime.utc_now() |> DateTime.to_iso8601(),
      message: details[:message] || details["message"],
      retry_at: details[:retry_at] || details["retry_at"]
    }

    File.write!(usage_limit_marker(workspace), Jason.encode!(payload))
  end

  defp build_turn_prompt(issue, opts, 1, _max_turns), do: PromptBuilder.build_prompt(issue, opts)
''',
        "usage-limit terminal worker handling",
    )

    print("RPG Kingdom Symphony usage-limit transform: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
