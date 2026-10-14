#!/usr/bin/env python3
"""Summarize the most recent failed broker preflight without publishing host secrets."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re


def sanitize(text: str) -> str:
    # Keep the actual filesystem error, request and phase but redact local paths.
    text = re.sub(r'\\\\wsl(?:\.localhost|\$)\\[^\s\)\;]+', '[WSL workspace path]', text, flags=re.I)
    text = re.sub(r'(?i)[A-Z]:\\(?:[^\s\)\;]+)', '[Windows local path]', text)
    text = re.sub(r'/home/[^\s\)\;]+', '[WSL local path]', text)
    text = re.sub(r'(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)', '[redacted token]', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:450]


def describe(root: Path, after: float, operation: str) -> str:
    response_dir = root / "Logs/SymphonyUnity/.broker/responses"
    if not response_dir.is_dir():
        return ""
    eligible = []
    for path in response_dir.glob("*.json"):
        try:
            if path.stat().st_mtime < after:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("operation") != operation or payload.get("status") not in ("failed", "NoTestsMatched"):
                continue
            eligible.append((path.stat().st_mtime, payload))
        except (OSError, json.JSONDecodeError, AttributeError):
            continue
    if not eligible:
        return ""
    payload = max(eligible, key=lambda item: item[0])[1]
    request_id = str(payload.get("requestId", ""))[:160]
    if not re.fullmatch(r"GH-\d+-[A-Za-z0-9_-]+", request_id):
        return ""
    stderr = str(payload.get("stderr") or "")
    phase = re.search(r'phase=([a-z0-9_]+)', stderr, re.I)
    observed_phase = phase.group(1) if phase else "unavailable"
    excerpt = sanitize(stderr)
    code = payload.get("exitCode")
    return (
        "\n\n### Managed Unity broker diagnostic\n"
        f"- Request: `{request_id}`\n"
        f"- Operation: `{operation}`\n"
        f"- Phase: `{observed_phase}`\n"
        f"- Broker status: `{payload.get('status')}` (exit `{code}`)\n"
        f"- Error excerpt (sanitized): `{excerpt or 'No stderr supplied'}`\n"
        "- Unity test result: **not confirmed** unless a trusted summary exists for this request.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--after-epoch", type=float, required=True)
    parser.add_argument("--operation", choices=["editmode", "playmode"], required=True)
    args = parser.parse_args()
    print(describe(args.workspace, args.after_epoch, args.operation), end="")


if __name__ == "__main__":
    main()
