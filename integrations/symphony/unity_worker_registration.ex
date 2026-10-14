defmodule SymphonyElixir.UnityWorkerRegistration do
  @moduledoc """
  Staged integration module for the separate local Symphony checkout.

  Copy into elixir/lib/symphony_elixir only after review of the installed
  Symphony branch. This module is intentionally not auto-loaded by Supervisor.
  It uses a controller-only Unix socket client; workers never write leases.
  """
  @run_id_pattern ~r/^GH-[0-9]+-[A-Za-z0-9_-]{1,160}$/

  @spec register(map(), String.t(), String.t(), keyword()) ::
          {:ok, map()} | {:error, term()}
  def register(session, issue_identifier, workspace, opts \\ []) do
    socket = Keyword.get(opts, :socket, System.get_env("RPGK_UNITY_WORKER_REGISTRAR_SOCKET"))
    script = Keyword.get(opts, :client_script, System.get_env("RPGK_UNITY_WORKER_REGISTRAR_CLIENT"))
    run_id = Keyword.get(opts, :run_id, make_run_id(issue_identifier))

    cond do
      not is_binary(socket) or socket == "" -> {:error, :registrar_not_configured}
      not is_binary(script) or script == "" -> {:error, :registrar_client_not_configured}
      not is_nil(Map.get(session, :worker_host)) -> {:error, :remote_worker_not_supported}
      not Regex.match?(@run_id_pattern, run_id) -> {:error, :invalid_worker_run_id}
      not is_binary(workspace) -> {:error, :invalid_workspace}
      true ->
        with {:ok, pid} <- session_pid(session),
             {:ok, response} <- execute(script, [
               "--socket", socket, "register",
               "--issue", issue_identifier, "--workspace", workspace,
               "--pid", Integer.to_string(pid), "--run-id", run_id
             ]),
             %{"registration" => %{"processStartTicks" => start_ticks,
                                   "pid" => ^pid, "runId" => ^run_id}} <- response
        do
          {:ok, %{pid: pid, run_id: run_id, start_ticks: start_ticks, script: script,
                  socket: socket}}
        else
          {:error, reason} -> {:error, reason}
          _ -> {:error, :invalid_registration_response}
        end
    end
  end

  @spec revoke(map()) :: :ok | {:error, term()}
  def revoke(%{pid: pid, run_id: run_id, start_ticks: ticks,
               script: script, socket: socket}) do
    case execute(script, [
      "--socket", socket, "revoke", "--pid", Integer.to_string(pid),
      "--run-id", run_id, "--process-start-ticks", ticks
    ]) do
      {:ok, %{"revoked" => _}} -> :ok
      {:ok, _} -> {:error, :invalid_revocation_response}
      error -> error
    end
  end

  defp session_pid(%{metadata: %{codex_app_server_pid: pid}}) when is_binary(pid) do
    case Integer.parse(pid) do
      {value, ""} when value > 1 -> {:ok, value}
      _ -> {:error, :invalid_session_pid}
    end
  end
  defp session_pid(_), do: {:error, :missing_session_pid}

  defp make_run_id(issue) when is_binary(issue) do
    issue <> "-" <> Base.url_encode64(:crypto.strong_rand_bytes(18), padding: false)
  end
  defp make_run_id(_), do: ""

  defp execute(script, args) do
    try do
      case System.cmd("python3", [script | args], stderr_to_stdout: false) do
        {output, 0} ->
          case Jason.decode(output) do
            {:ok, payload} when is_map(payload) -> {:ok, payload}
            _ -> {:error, :invalid_registrar_reply}
          end
        {_, code} -> {:error, {:registrar_rejected, code}}
      end
    rescue
      e in ErlangError -> {:error, {:registrar_unavailable, e.original}}
    end
  end
end
