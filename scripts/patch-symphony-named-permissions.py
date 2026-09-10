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
        print("usage: patch-symphony-named-permissions.py <symphony-elixir-root>", file=sys.stderr)
        return 64

    root = Path(sys.argv[1]).resolve()
    schema = root / "lib/symphony_elixir/config/schema.ex"
    config = root / "lib/symphony_elixir/config.ex"
    app_server = root / "lib/symphony_elixir/codex/app_server.ex"

    for path in (schema, config, app_server):
        if not path.is_file():
            raise RuntimeError(f"expected Symphony source file is missing: {path}")

    replace_once(
        schema,
        '      field(:thread_sandbox, :string, default: "workspace-write")\n',
        '      field(:permissions, :string)\n      field(:thread_sandbox, :string, default: "workspace-write")\n',
        "codex.permissions schema field",
    )
    replace_once(
        schema,
        '          :command,\n          :approval_policy,\n          :thread_sandbox,\n',
        '          :command,\n          :approval_policy,\n          :permissions,\n          :thread_sandbox,\n',
        "codex.permissions changeset field",
    )

    replace_once(
        config,
        '          approval_policy: String.t() | map(),\n          thread_sandbox: String.t(),\n',
        '          approval_policy: String.t() | map(),\n          permissions: String.t() | nil,\n          thread_sandbox: String.t(),\n',
        "runtime permissions type",
    )
    replace_once(
        config,
        '           approval_policy: settings.codex.approval_policy,\n           thread_sandbox: settings.codex.thread_sandbox,\n',
        '           approval_policy: settings.codex.approval_policy,\n           permissions: settings.codex.permissions,\n           thread_sandbox: settings.codex.thread_sandbox,\n',
        "runtime permissions value",
    )

    replace_once(
        app_server,
        '          auto_approve_requests: boolean(),\n          thread_sandbox: String.t(),\n',
        '          auto_approve_requests: boolean(),\n          permissions: String.t() | nil,\n          thread_sandbox: String.t(),\n',
        "session permissions type",
    )
    replace_once(
        app_server,
        '           auto_approve_requests: session_policies.approval_policy == "never",\n           thread_sandbox: session_policies.thread_sandbox,\n',
        '           auto_approve_requests: session_policies.approval_policy == "never",\n           permissions: session_policies.permissions,\n           thread_sandbox: session_policies.thread_sandbox,\n',
        "session permissions value",
    )
    replace_once(
        app_server,
        '          auto_approve_requests: auto_approve_requests,\n          turn_sandbox_policy: turn_sandbox_policy,\n',
        '          auto_approve_requests: auto_approve_requests,\n          permissions: permissions,\n          turn_sandbox_policy: turn_sandbox_policy,\n',
        "run_turn permissions binding",
    )
    replace_once(
        app_server,
        '    case start_turn(port, thread_id, prompt, issue, workspace, approval_policy, turn_sandbox_policy) do\n',
        '    case start_turn(\n'
        '           port,\n'
        '           thread_id,\n'
        '           prompt,\n'
        '           issue,\n'
        '           workspace,\n'
        '           approval_policy,\n'
        '           permissions,\n'
        '           turn_sandbox_policy\n'
        '         ) do\n',
        "start_turn permissions argument",
    )

    old_block = '''  defp start_thread(
         port,
         workspace,
         %{approval_policy: approval_policy, thread_sandbox: thread_sandbox},
         dynamic_tool_binding
       ) do
    send_message(port, %{
      "method" => "thread/start",
      "id" => @thread_start_id,
      "params" => %{
        "approvalPolicy" => approval_policy,
        "sandbox" => thread_sandbox,
        "cwd" => workspace,
        "dynamicTools" => dynamic_tool_binding.tool_specs
      }
    })

    case await_response(port, @thread_start_id) do
      {:ok, %{"thread" => thread_payload}} ->
        case thread_payload do
          %{"id" => thread_id} -> {:ok, thread_id}
          _ -> {:error, {:invalid_thread_payload, thread_payload}}
        end

      other ->
        other
    end
  end

  defp start_turn(port, thread_id, prompt, issue, workspace, approval_policy, turn_sandbox_policy) do
    send_message(port, %{
      "method" => "turn/start",
      "id" => @turn_start_id,
      "params" => %{
        "threadId" => thread_id,
        "input" => [
          %{
            "type" => "text",
            "text" => prompt
          }
        ],
        "cwd" => workspace,
        "title" => "#{issue.identifier}: #{issue.title}",
        "approvalPolicy" => approval_policy,
        "sandboxPolicy" => turn_sandbox_policy
      }
    })

    case await_response(port, @turn_start_id) do
      {:ok, %{"turn" => %{"id" => turn_id}}} -> {:ok, turn_id}
      other -> other
    end
  end
'''

    new_block = '''  defp start_thread(
         port,
         workspace,
         %{
           approval_policy: approval_policy,
           permissions: permissions,
           thread_sandbox: thread_sandbox
         },
         dynamic_tool_binding
       ) do
    params =
      %{
        "approvalPolicy" => approval_policy,
        "cwd" => workspace,
        "runtimeWorkspaceRoots" => [workspace],
        "dynamicTools" => dynamic_tool_binding.tool_specs
      }
      |> put_thread_permissions(permissions, thread_sandbox)

    send_message(port, %{
      "method" => "thread/start",
      "id" => @thread_start_id,
      "params" => params
    })

    case await_response(port, @thread_start_id) do
      {:ok, %{"thread" => thread_payload}} ->
        case thread_payload do
          %{"id" => thread_id} -> {:ok, thread_id}
          _ -> {:error, {:invalid_thread_payload, thread_payload}}
        end

      other ->
        other
    end
  end

  defp put_thread_permissions(params, permissions, _thread_sandbox)
       when is_binary(permissions) and permissions != "" do
    Map.put(params, "permissions", permissions)
  end

  defp put_thread_permissions(params, _permissions, thread_sandbox) do
    Map.put(params, "sandbox", thread_sandbox)
  end

  defp start_turn(
         port,
         thread_id,
         prompt,
         issue,
         workspace,
         approval_policy,
         permissions,
         turn_sandbox_policy
       ) do
    params =
      %{
        "threadId" => thread_id,
        "input" => [
          %{
            "type" => "text",
            "text" => prompt
          }
        ],
        "cwd" => workspace,
        "runtimeWorkspaceRoots" => [workspace],
        "title" => "#{issue.identifier}: #{issue.title}",
        "approvalPolicy" => approval_policy
      }
      |> put_turn_permissions(permissions, turn_sandbox_policy)

    send_message(port, %{
      "method" => "turn/start",
      "id" => @turn_start_id,
      "params" => params
    })

    case await_response(port, @turn_start_id) do
      {:ok, %{"turn" => %{"id" => turn_id}}} -> {:ok, turn_id}
      other -> other
    end
  end

  defp put_turn_permissions(params, permissions, _turn_sandbox_policy)
       when is_binary(permissions) and permissions != "" do
    Map.put(params, "permissions", permissions)
  end

  defp put_turn_permissions(params, _permissions, turn_sandbox_policy) do
    Map.put(params, "sandboxPolicy", turn_sandbox_policy)
  end
'''
    replace_once(app_server, old_block, new_block, "thread/turn named-permissions block")

    print("RPG Kingdom Symphony source transform: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
