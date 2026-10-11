#!/usr/bin/env python3
"""Observer contracts plus real SDK stdio/HTTP protocol interoperability."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import supervisor_mcp as observer

TOOLS = {
    "supervisor_status", "supervisor_work", "supervisor_issue", "supervisor_worker",
    "supervisor_unity_runs", "supervisor_unity_run", "supervisor_usage", "supervisor_autonomous",
}


def payload(result):
    # FastMCP returns text + structured content for a dict annotation.
    if isinstance(result, tuple):
        return result[1]
    if isinstance(result, dict):
        return result
    return json.loads(result[0].text)


class Contracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = observer.create_server()

    async def test_tool_surface_and_schemas(self):
        tools = await self.server.list_tools()
        self.assertEqual({tool.name for tool in tools}, TOOLS)
        for tool in tools:
            self.assertTrue(tool.annotations.readOnlyHint)
            self.assertFalse(tool.annotations.destructiveHint)
        issue = next(tool for tool in tools if tool.name == "supervisor_issue")
        self.assertEqual(issue.inputSchema["properties"]["issue"]["minimum"], 1)
        self.assertEqual(self.server.settings.host, "127.0.0.1")
        self.assertTrue(self.server.settings.transport_security.enable_dns_rebinding_protection)

    async def test_collectors_and_freshness_are_preserved(self):
        sample = {"services": {"review": {"state": "stopped"}}, "quota": {"status": "stale"}}
        with patch.object(observer.supervisor_telemetry, "collect_operations", return_value=sample):
            result = payload(await self.server.call_tool("supervisor_status", {}))
            self.assertEqual(result["data"], sample)
        with patch.object(observer.diagnostics, "collect", return_value={"github": {"available": False}}) as collect:
            with patch.object(observer.review_state, "collect", return_value={"available": False}):
                result = payload(await self.server.call_tool("supervisor_issue", {"issue": 255}))
                collect.assert_called_once_with(255)
                self.assertFalse(result["data"]["github"]["available"])
        with patch.object(observer.supervisor_detail, "collect", return_value={"worker": {"quotaAfter": {"old": True}}}):
            with patch.object(observer.supervisor_telemetry, "current_quota", return_value={"status": "unavailable"}):
                result = payload(await self.server.call_tool("supervisor_worker", {"run_id": "run-1"}))
                self.assertEqual(result["data"]["currentGlobalQuota"]["status"], "unavailable")
                self.assertTrue(result["data"]["worker"]["quotaAfter"]["old"])
        with patch.object(observer.unity_run_history, "collect_runs", return_value=[]) as collect:
            await self.server.call_tool("supervisor_unity_runs", {"issue": 255, "limit": 5})
            collect.assert_called_once_with(issue="GH-255", limit=5)
        with patch.object(observer.unity_run_history, "find_run", return_value={"status": "NoTestsMatched"}):
            result = payload(await self.server.call_tool("supervisor_unity_run", {"request_id": "request-1"}))
            self.assertEqual(result["data"]["status"], "NoTestsMatched")
        with patch.object(observer.supervisor_usage_analysis, "analyze", return_value={"workerCount": 0}) as collect:
            await self.server.call_tool("supervisor_usage", {"limit": 5})
            collect.assert_called_once_with(limit=5)

    async def test_pagination_and_warming_cache(self):
        rows = [{"issue": index} for index in range(1, 61)]
        snapshot = {"items": rows, "queues": {"halted": rows}, "activity": rows, "available": True, "cache": {"stale": True}}
        with patch.object(observer.supervisor_activity, "collect", return_value=snapshot):
            result = payload(await self.server.call_tool("supervisor_work", {"limit": 10, "offset": 50}))["data"]
            self.assertEqual(len(result["items"]), 10)
            self.assertEqual(result["total"], 60)
            self.assertIsNone(result["nextOffset"])
            self.assertEqual(result["queueCounts"], {"halted": 60})
            self.assertEqual(result["cache"], {"stale": True})
        with patch.object(observer.supervisor_activity, "collect", return_value={"available": False, "errors": ["warming"]}):
            result = payload(await self.server.call_tool("supervisor_work", {}))["data"]
            self.assertFalse(result["available"])
            self.assertEqual(result["errors"], ["warming"])

    async def test_invalid_arguments_never_reach_collectors(self):
        with patch.object(observer.supervisor_detail, "collect") as collect:
            for run_id in ("../quota", "/etc/passwd", "a/b", "x" * 129):
                with self.assertRaises(Exception):
                    await self.server.call_tool("supervisor_worker", {"run_id": run_id})
            collect.assert_not_called()
        for name, arguments in (
            ("supervisor_issue", {"issue": 0}), ("supervisor_issue", {"issue": True}),
            ("supervisor_work", {"limit": 51}), ("supervisor_work", {"offset": -1}),
            ("supervisor_usage", {"limit": 501}), ("merge", {}), ("rearm", {}),
        ):
            with self.assertRaises(Exception):
                await self.server.call_tool(name, arguments)
        with patch.object(observer.supervisor_detail, "collect", return_value=None):
            with self.assertRaisesRegex(Exception, "not found"):
                await self.server.call_tool("supervisor_worker", {"run_id": "unknown"})

    async def test_redaction_and_explicit_truncation(self):
        secret = "test-token-never-export"
        with patch.dict(os.environ, {"CONTROL_PLANE_API_KEY": secret}):
            result = observer.response({"message": secret, "authorization": "private", "rows": list(range(100)), "log": "x" * 5000})
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(result["data"]["authorization"], "[REDACTED]")
        self.assertEqual(len(result["data"]["rows"]), 50)
        self.assertEqual(result["truncatedPaths"], ["data.rows", "data.log"])
        with self.assertRaisesRegex(ValueError, "128 KiB"):
            observer.response({f"log{i}": "x" * 4000 for i in range(40)})

    async def test_autonomous_missing_corrupt_and_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(observer.supervisor_telemetry, "state_root", return_value=root):
                result = payload(await self.server.call_tool("supervisor_autonomous", {}))
                self.assertFalse(result["data"]["available"])
                path = root / "autonomous-status.json"
                path.write_text("not json")
                result = payload(await self.server.call_tool("supervisor_autonomous", {}))
                self.assertFalse(result["data"]["available"])
                path.write_text('{"decision":{"state":"paused","dispatch":false}}')
                result = payload(await self.server.call_tool("supervisor_autonomous", {}))
                self.assertEqual(result["data"]["decision"]["state"], "paused")

    async def test_stdio_protocol_and_no_observer_state_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            state.mkdir()
            (state / "autonomous-status.json").write_text('{"decision":{"state":"paused"}}')
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            env = dict(os.environ, RPGK_SUPERVISOR_STATE_ROOT=str(state), RPGK_WORKSPACE_ROOT=str(root / "workspaces"), RPGK_SUPERVISOR_ROOT=str(ROOT), RPGK_MCP_PYTHON=sys.executable)
            params = StdioServerParameters(command="bash", args=[str(ROOT / "scripts/serve-mcp.sh")], env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.assertEqual({t.name for t in (await session.list_tools()).tools}, TOOLS)
                    for name in ("supervisor_status", "supervisor_autonomous", "supervisor_usage", "supervisor_unity_runs"):
                        result = await session.call_tool(name, {})
                        self.assertFalse(result.isError, result)
                        self.assertIn("observedAt", result.structuredContent)
                    result = await session.call_tool("supervisor_worker", {"run_id": "../escape"})
                    self.assertTrue(result.isError)
            after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)

    async def test_streamable_http_protocol_and_host_guard(self):
        app = self.server.streamable_http_app()
        async with self.server.session_manager.run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
                headers = {"Accept": "application/json, text/event-stream"}
                message = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
                blocked = await http.post("http://evil.example/mcp", headers=headers, json=message)
                self.assertEqual(blocked.status_code, 421)
                blocked = await http.post("http://127.0.0.1:8766/mcp", headers={**headers, "Origin": "https://evil.example"}, json=message)
                self.assertEqual(blocked.status_code, 403)
                async with streamable_http_client("http://127.0.0.1:8766/mcp", http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self.assertEqual({t.name for t in (await session.list_tools()).tools}, TOOLS)
                        with patch.object(observer.supervisor_telemetry, "collect_operations", return_value={"quota": {"status": "stale"}}):
                            result = await session.call_tool("supervisor_status", {})
                            self.assertFalse(result.isError)
                            self.assertEqual(result.structuredContent["data"]["quota"]["status"], "stale")


if __name__ == "__main__":
    unittest.main()
