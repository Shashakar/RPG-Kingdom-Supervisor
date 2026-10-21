#!/usr/bin/env python3
"""Read-only identity for the implementation provider, not the reviewer model.

Prefer execution evidence (Codex telemetry / Claude pilot and dispatch journal).
A GitHub agent label describes assignment, not proof that an agent ran.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

CLAUDE_MODELS = {"haiku", "sonnet", "opus"}
CODEX_MODEL_NAMES = {"luna", "terra", "sol", "astra"}


def label_set(labels: Iterable[Any]) -> set[str]:
    result: set[str] = set()
    for entry in labels:
        text = entry.get("name") if isinstance(entry, dict) else entry
        if isinstance(text, str) and text.strip():
            result.add(text.strip().lower())
    return result


def assigned_provider(labels: Iterable[Any]) -> str | None:
    names = label_set(labels)
    if "agent:claude" in names and "agent:codex" not in names and "agent:auto" not in names:
        return "claude"
    if "agent:codex" in names and "agent:claude" not in names and "agent:auto" not in names:
        return "codex"
    return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 80_000:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def claude_execution(issue: int, repo: str, state_root: Path | None = None) -> dict[str, Any]:
    if repo != "Shashakar/Rekindle-Claude" or not isinstance(issue, int) or issue <= 0:
        return {}
    root = state_root or Path(os.path.expanduser(
        os.environ.get("RPGK_SUPERVISOR_STATE_ROOT", "~/.local/state/rekindle-supervisor")))
    identifier = f"GH-{issue}"
    branch = f"claude/gh-{issue}-implementation"
    pilot = _read_json(root / "claude-pilot" / identifier / "latest.json")
    if (pilot.get("issue") == identifier and pilot.get("repository") == repo
            and pilot.get("branch") == branch and pilot.get("status") == "completed"):
        return {
            "provider": "claude", "source": "claude-pilot",
            "executed": True, "model": pilot.get("requestedModel"),
            "effort": pilot.get("requestedEffort"),
            "reportedCostUsd": pilot.get("reportedCostUsd"),
        }
    journal = _read_json(root / "claude-dispatch" / f"{identifier}.json")
    if (journal.get("issue") == issue and journal.get("branch") == branch
            and journal.get("stage") in {"executed", "visual_started", "visual_evidence", "handoff", "review"}
            and journal.get("model") in CLAUDE_MODELS):
        return {
            "provider": "claude", "source": "claude-dispatch",
            "executed": True, "model": journal.get("model"),
            "effort": journal.get("effort"),
            "reportedCostUsd": journal.get("reportedCostUsd"),
        }
    return {}


def resolve(
    *, labels: Iterable[Any], issue: int | None = None, repo: str = "",
    worker: dict[str, Any] | None = None, state_root: Path | None = None,
) -> dict[str, Any]:
    """Return provider / model / effort and whether execution is evidenced."""
    names = label_set(labels)
    if worker and worker.get("runId"):
        # The host's Codex worker ledger is Codex-specific; old records have no
        # provider key. Do not reassign an already-executed Codex worker by labels.
        provider = worker.get("provider") or "codex"
        if provider in {"codex", "claude"}:
            return {"provider": provider, "source": "worker-telemetry",
                    "executed": True, "model": worker.get("model"),
                    "effort": worker.get("effort")}
    if issue is not None:
        executed = claude_execution(issue, repo, state_root)
        if executed:
            return executed
    provider = assigned_provider(names)
    if provider == "claude":
        models = [x.split(":", 1)[1] for x in names if x.startswith("claude-model:")]
        efforts = [x.split(":", 1)[1] for x in names if x.startswith("claude-effort:")]
        return {
            "provider": "claude", "source": "issue-labels", "executed": False,
            "model": models[0] if len(models) == 1 and models[0] in CLAUDE_MODELS else None,
            "effort": efforts[0] if len(efforts) == 1 and efforts[0] in {"low", "medium", "high"} else None,
        }
    if provider == "codex":
        return {"provider": "codex", "source": "issue-labels", "executed": False,
                "model": None, "effort": None}
    return {"provider": None, "source": "unknown", "executed": False,
            "model": None, "effort": None}
