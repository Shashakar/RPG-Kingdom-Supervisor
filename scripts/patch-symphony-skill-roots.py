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
        print("usage: patch-symphony-skill-roots.py <symphony-elixir-root>", file=sys.stderr)
        return 64

    root = Path(sys.argv[1]).resolve()
    app_server = root / "lib/symphony_elixir/codex/app_server.ex"
    agent_runner = root / "lib/symphony_elixir/agent_runner.ex"
    for path in (app_server, agent_runner):
        if not path.is_file():
            raise RuntimeError(f"expected Symphony source file is missing: {path}")

    replace_once(
        app_server,
        "  @turn_start_id 3\n",
        "  @turn_start_id 3\n  @skills_extra_roots_id 4\n",
        "skill-root request id",
    )

    replace_once(
        app_server,
        '''  defp session_policies(workspace, nil) do
    Config.codex_runtime_settings(workspace)
  end
''',
        '''  defp configure_supervisor_skill_roots(port, workspace) do
    if Regex.match?(~r/^GH-\\d+$/, Path.basename(workspace)) do
      supervisor_root =
        System.get_env("RPGK_SUPERVISOR_ROOT") || Path.expand("~/src/RPG-Kingdom-Supervisor")

      skill_root =
        System.get_env("RPGK_SUPERVISOR_SKILLS_ROOT") || Path.join(supervisor_root, "skills")

      send_message(port, %{
        "method" => "skills/extraRoots/set",
        "id" => @skills_extra_roots_id,
        "params" => %{"extraRoots" => [skill_root]}
      })

      case await_response(port, @skills_extra_roots_id) do
        {:ok, _} -> :ok
        other -> other
      end
    else
      :ok
    end
  end

  defp session_policies(workspace, nil) do
    Config.codex_runtime_settings(workspace)
  end
''',
        "skill-root helper",
    )

    replace_once(
        app_server,
        '''  defp do_start_session(port, workspace, session_policies, dynamic_tool_binding) do
    case send_initialize(port) do
      :ok -> start_thread(port, workspace, session_policies, dynamic_tool_binding)
      {:error, reason} -> {:error, reason}
    end
  end
''',
        '''  defp do_start_session(port, workspace, session_policies, dynamic_tool_binding) do
    with :ok <- send_initialize(port),
         :ok <- configure_supervisor_skill_roots(port, workspace) do
      start_thread(port, workspace, session_policies, dynamic_tool_binding)
    end
  end
''',
        "session skill-root initialization",
    )

    replace_once(
        agent_runner,
        '''  defp build_turn_prompt(issue, opts, 1, _max_turns), do: PromptBuilder.build_prompt(issue, opts)
''',
        '''  defp build_turn_prompt(issue, opts, 1, _max_turns) do
    issue
    |> maybe_route_first_party_skill(PromptBuilder.build_prompt(issue, opts))
  end

  defp maybe_route_first_party_skill(issue, prompt) do
    labels = Map.get(issue, :labels, []) || []

    if Enum.any?(labels, &(String.downcase(to_string(&1)) == "risk:investigative")) do
      "$rpgk-investigate-bug\\n\\n" <> prompt
    else
      prompt
    end
  end
''',
        "investigative first-turn skill routing",
    )

    print("RPG Kingdom Symphony first-party skill-root transform: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
