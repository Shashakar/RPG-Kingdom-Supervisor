#!/usr/bin/env python3
"""Pure, deterministic Claude model/effort routing for operator-approved pilot runs.

This is separate from the Codex Luna/Sol policy and does not launch workers.
"""
from __future__ import annotations

import json
from pathlib import Path

MODELS = ("haiku", "sonnet", "opus")
EFFORTS = ("low", "medium", "high")
RISKS = (
    "risk:mechanical",
    "risk:normal",
    "risk:investigative",
    "risk:architecture",
    "risk:end-to-end",
)


class ClaudeModelPolicyError(ValueError):
    pass


def read_labels(path: str | None) -> list[str]:
    """Accept gh issue view --json labels or a JSON array of label names."""
    if not path:
        return []
    source = Path(path).expanduser()
    if not source.is_file() or source.stat().st_size > 32_768:
        raise ClaudeModelPolicyError("labels file missing or larger than 32 KiB")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ClaudeModelPolicyError("labels file is not valid JSON") from exc
    if isinstance(value, dict):
        value = value.get("labels")
    if not isinstance(value, list) or len(value) > 100:
        raise ClaudeModelPolicyError("labels must be a JSON list of at most 100 items")
    result: list[str] = []
    for item in value:
        if isinstance(item, dict):
            item = item.get("name")
        if not isinstance(item, str) or not item.strip() or len(item) > 128:
            raise ClaudeModelPolicyError("invalid label entry")
        result.append(item.strip().lower())
    return result


def resolve(
    labels: list[str], *,
    cli_model: str | None = None,
    cli_effort: str | None = None,
) -> dict[str, str]:
    selected = {name.lower() for name in labels}
    model_labels = [name for name in MODELS if f"claude-model:{name}" in selected]
    effort_labels = [name for name in EFFORTS if f"claude-effort:{name}" in selected]
    risk_labels = [name for name in RISKS if name in selected]
    if any(label.startswith("claude-model:") and label not in
           {f"claude-model:{name}" for name in MODELS} for label in selected):
        raise ClaudeModelPolicyError("unrecognized claude-model:* label")
    if any(label.startswith("claude-effort:") and label not in
           {f"claude-effort:{name}" for name in EFFORTS} for label in selected):
        raise ClaudeModelPolicyError("unrecognized claude-effort:* label")
    if len(model_labels) > 1 or len(effort_labels) > 1:
        raise ClaudeModelPolicyError("conflicting Claude model or effort labels")
    if len(risk_labels) > 1:
        raise ClaudeModelPolicyError("conflicting risk labels")
    if cli_model is not None and cli_model not in MODELS:
        raise ClaudeModelPolicyError("unsupported CLI model (haiku, sonnet, opus only)")
    if cli_effort is not None and cli_effort not in EFFORTS:
        raise ClaudeModelPolicyError("unsupported CLI effort (low, medium, high only)")

    risk = risk_labels[0] if risk_labels else ""
    # Visual design without a risk label still deserves sustained review.
    if risk == "risk:mechanical":
        default_model, default_effort = "haiku", "low"
    elif risk == "risk:investigative":
        default_model, default_effort = "opus", "medium"
    elif risk in ("risk:architecture", "risk:end-to-end"):
        default_model, default_effort = "opus", "high"
    elif "work:visual-unity" in selected:
        default_model, default_effort = "sonnet", "high"
    else:
        default_model, default_effort = "sonnet", "medium"

    return {
        "model": cli_model or (model_labels[0] if model_labels else default_model),
        "effort": cli_effort or (effort_labels[0] if effort_labels else default_effort),
        "modelSource": "operator-cli" if cli_model else ("issue-label" if model_labels else "policy"),
        "effortSource": "operator-cli" if cli_effort else ("issue-label" if effort_labels else "policy"),
    }
