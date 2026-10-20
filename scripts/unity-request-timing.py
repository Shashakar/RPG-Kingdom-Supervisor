#!/usr/bin/env python3
"""Read-only timing diagnosis for a managed Unity broker request."""
import argparse
from datetime import datetime
import json
from pathlib import Path


def load(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def epoch(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def diagnose(workspace, request_id):
    if not request_id.startswith(workspace.name + "-") or not all(
        character.isalnum() or character in "-_" for character in request_id
    ):
        raise ValueError("request ID must belong to the supplied workspace")
    root = workspace / "Logs" / "SymphonyUnity" / ".broker"
    request = load(root / "history" / "requests" / (request_id + ".json"))
    if not request or request.get("requestId") != request_id:
        raise ValueError("matching request history unavailable")
    ack = load(root / "acks" / (request_id + ".json"))
    progress = load(root / "progress" / (request_id + ".json"))
    response = load(root / "responses" / (request_id + ".json"))
    times = {
        "requested": epoch(request.get("requestedAt")),
        "accepted": epoch((ack or {}).get("acceptedAt")),
        "lastProgress": epoch((progress or {}).get("observedAt")),
        "completed": epoch((response or {}).get("completedAt")),
    }
    delta = lambda a, b: (round(times[b] - times[a], 2)
                          if times[a] is not None and times[b] is not None else None)
    details = (response or {}).get("details") or {}
    active = details.get("activeRequest") or {}
    return {
        "requestId": request_id,
        "operation": request.get("operation"),
        "terminalStatus": (response or {}).get("status"),
        "exitCode": (response or {}).get("exitCode"),
        "timingsSeconds": {
            "queueToAcceptance": delta("requested", "accepted"),
            "acceptanceToLastProgress": delta("accepted", "lastProgress"),
            "lastProgressToTerminal": delta("lastProgress", "completed"),
            "requestToTerminal": delta("requested", "completed"),
        },
        "lastProgress": {
            "phase": (progress or {}).get("phase"),
            "sequence": (progress or {}).get("sequence"),
            "unityPid": (progress or {}).get("unityPid"),
            "editorLogBytes": (progress or {}).get("editorLogBytes"),
            "summaryPresent": (progress or {}).get("summaryPresent"),
        },
        "ownership": {
            "workerRunId": active.get("workerRunId"),
            "workerPid": active.get("workerPid"),
            "reason": details.get("reason"),
            "recoveryResult": details.get("recoveryResult"),
        },
        "limits": "Only the latest progress snapshot is retained; intermediate phase durations cannot be reconstructed.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(diagnose(args.workspace.resolve(strict=True), args.request_id), indent=2))
    except ValueError as exc:
        parser.error(str(exc))
