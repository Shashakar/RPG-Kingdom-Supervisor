#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "codex-capability-policy.py"
spec = importlib.util.spec_from_file_location("codex_capability_policy", SCRIPT)
assert spec and spec.loader
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

old_env = os.environ.copy()
try:
    os.environ["RPGK_SUPERVISOR_ROOT"] = str(ROOT)
    os.environ.pop("RPGK_GRAPHIFY_ENABLED", None)
    os.environ.pop("RPGK_GRAPHIFY_ALLOW_STALE", None)
    os.environ.pop("RPGK_GRAPHIFY_EXPECTED_VERSION", None)
    os.environ.pop("RPGK_CONTEXT7_URL", None)
    # Keep Graphify fixtures isolated; Context7 gets its own routing assertions below.
    os.environ["RPGK_CONTEXT7_ENABLED"] = "off"

    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        repo = temp_path / "RPG-Kingdom"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repo, check=True)

        graph = repo / "graphify-out" / "graph.json"
        graph.parent.mkdir()
        graph.write_text("{}\n", encoding="utf-8")
        future = time.time() + 5
        os.utime(graph, (future, future))

        bin_dir = temp_path / "bin"
        bin_dir.mkdir()
        graphify = bin_dir / "graphify"
        graphify.write_text("#!/usr/bin/env bash\nprintf 'graphify 0.9.58\\n'\n", encoding="utf-8")
        graphify.chmod(0o755)
        graphify_mcp = bin_dir / "graphify-mcp"
        graphify_mcp.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        graphify_mcp.chmod(0o755)

        os.environ["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        os.environ["RPGK_GRAPHIFY_REPO_ROOT"] = str(repo)
        os.environ["RPGK_GRAPHIFY_GRAPH"] = str(graph)
        os.environ["RPGK_GRAPHIFY_MCP_COMMAND"] = str(graphify_mcp)

        payload, args = policy.route_payload("terra", "GH-108", temp_path, ["risk:investigative"])
        assert payload["selectedSkills"] == ["rpgk-investigate-bug"]
        graph_state = payload["mcp"][0]
        assert graph_state["enabled"] is True, graph_state
        assert graph_state["installedVersion"] == "0.9.58"
        assert graph_state["expectedVersion"] == "0.9.58"
        assert graph_state["readOnly"] is True
        assert graph_state["freshness"]["fresh"] is True
        assert payload["mcp"][1]["name"] == "context7"
        assert payload["mcp"][1]["enabled"] is False
        assert any("mcp_servers.rpgk_graphify.command" in value for value in args)
        assert any(str(graph) in value for value in args)

        # A mismatched third-party version is not silently accepted in auto mode.
        graphify.write_text("#!/usr/bin/env bash\nprintf 'graphify 0.9.57\\n'\n", encoding="utf-8")
        graphify.chmod(0o755)
        payload, mismatch_args = policy.route_payload("terra", "GH-107", temp_path, ["risk:investigative"])
        assert payload["mcp"][0]["enabled"] is False
        assert "does not match evaluated version" in payload["mcp"][0]["reason"]
        assert mismatch_args == []
        graphify.write_text("#!/usr/bin/env bash\nprintf 'graphify 0.9.58\\n'\n", encoding="utf-8")
        graphify.chmod(0o755)

        # An explicit Terra model on a normal issue gets Graphify by route but not the
        # investigate-bug procedural skill, which is task-specific rather than model-specific.
        payload, _ = policy.route_payload("terra", "GH-109", temp_path, ["risk:normal", "model:terra"])
        assert payload["selectedSkills"] == []
        assert payload["mcp"][0]["enabled"] is True

        payload, args = policy.route_payload("luna", "GH-200", temp_path, ["risk:normal"])
        assert payload["selectedSkills"] == []
        assert payload["mcp"][0]["enabled"] is False
        assert payload["mcp"][1]["enabled"] is False
        assert args == []

        # The canonical graph must represent the same main revision the worker cloned. A freshly
        # rebuilt graph over a newer canonical checkout is still the wrong graph for an older
        # worker workspace, so auto mode refuses it rather than silently mixing revisions.
        worker = temp_path / "GH-210"
        subprocess.run(["git", "clone", "-q", str(repo), str(worker)], check=True)
        (repo / "README.md").write_text("fixture\nnew canonical change\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "new-main"], cwd=repo, check=True)
        os.utime(graph, (time.time() + 10, time.time() + 10))
        payload, args = policy.route_payload("terra", "GH-210", worker, ["risk:investigative"])
        freshness = payload["mcp"][0]["freshness"]
        assert freshness["fresh"] is False
        assert freshness["workerOriginMain"] is not None
        assert "does not match this worker's origin/main" in freshness["reason"]
        assert payload["mcp"][0]["enabled"] is False
        assert args == []

        os.utime(graph, (1, 1))
        payload, args = policy.route_payload("terra", "GH-201", temp_path, ["risk:investigative"])
        assert payload["mcp"][0]["enabled"] is False
        assert payload["mcp"][0]["freshness"]["status"] == "stale"
        assert args == []

        os.environ["RPGK_GRAPHIFY_ENABLED"] = "on"
        try:
            policy.route_payload("terra", "GH-202", temp_path, ["risk:investigative"])
        except RuntimeError as exc:
            assert "explicitly required" in str(exc)
        else:
            raise AssertionError("explicitly required stale Graphify configuration did not fail")

        os.environ["RPGK_GRAPHIFY_ENABLED"] = "auto"
        os.utime(graph, (time.time() + 10, time.time() + 10))
        payload, args = policy.route_payload("sol", "GH-203", temp_path, ["risk:architecture"])
        assert payload["selectedSkills"] == []
        args_path = temp_path / "args.bin"
        policy.write_args(args_path, args)
        decoded = args_path.read_bytes().split(b"\0")
        assert decoded[-1] == b""
        assert decoded[:-1] == [item.encode("utf-8") for item in args]

        # Context7 is offered only to deep routes in auto mode. It is a remote read-only docs
        # endpoint, so Supervisor neither installs it nor passes an API credential.
        os.environ["RPGK_GRAPHIFY_ENABLED"] = "off"
        os.environ["RPGK_CONTEXT7_ENABLED"] = "auto"
        payload, args = policy.route_payload("terra", "GH-300", temp_path, ["risk:investigative"])
        context7 = next(item for item in payload["mcp"] if item["name"] == "context7")
        assert context7["enabled"] is True
        assert context7["readOnly"] is True
        assert context7["purpose"] == "external-library-documentation"
        assert context7["credentialsProvidedBySupervisor"] is False
        assert context7["url"] == "https://mcp.context7.com/mcp"
        assert any("mcp_servers.rpgk_context7.url" in value for value in args)
        assert not any("api_key" in value.lower() or "authorization" in value.lower() for value in args)

        payload, args = policy.route_payload("luna", "GH-301", temp_path, ["risk:normal"])
        context7 = next(item for item in payload["mcp"] if item["name"] == "context7")
        assert context7["enabled"] is False
        assert args == []

        os.environ["RPGK_CONTEXT7_ENABLED"] = "on"
        payload, args = policy.route_payload("luna", "GH-302", temp_path, ["risk:mechanical"])
        assert next(item for item in payload["mcp"] if item["name"] == "context7")["enabled"] is True
        assert any("mcp_servers.rpgk_context7.url" in value for value in args)

        os.environ["RPGK_CONTEXT7_URL"] = "http://insecure.example/mcp"
        try:
            policy.route_payload("luna", "GH-303", temp_path, ["risk:mechanical"])
        except RuntimeError as exc:
            assert "absolute https URL" in str(exc)
        else:
            raise AssertionError("explicit insecure Context7 configuration did not fail")
finally:
    os.environ.clear()
    os.environ.update(old_env)

print("codex-capability-policy-test: PASS")
