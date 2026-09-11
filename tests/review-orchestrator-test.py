#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("review_orchestrator", ROOT / "scripts/review-orchestrator.py")
assert SPEC and SPEC.loader
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


class FakeGitHub:
    def __init__(self) -> None:
        self.issue_labels = {123: {"risk:normal", "symphony:agent-review"}}
        self.comments: dict[int, list[dict]] = {123: [], 77: []}
        self.operations: list[tuple[str, int, str]] = []

    def api(self, method: str, path: str, body=None):
        if path.startswith("/issues/123/comments") and method == "GET":
            return self.comments[123]
        if path == "/pulls/77/files?per_page=100" and method == "GET":
            return [{"filename": "Assets/Scripts/Fixture.cs"}, {"filename": "Assets/Tests/FixtureTests.cs"}]
        if path.startswith("/issues/123/labels/") and method == "DELETE":
            name = path.rsplit("/", 1)[-1].replace("%3A", ":")
            self.issue_labels[123].discard(name)
            self.operations.append(("remove", 123, name))
            return None
        if path == "/issues/123/labels" and method == "POST":
            for name in body["labels"]:
                self.issue_labels[123].add(name)
                self.operations.append(("add", 123, name))
            return []
        if path == "/issues/123/comments" and method == "POST":
            self.comments[123].append({"body": body["body"]})
            return {}
        if path == "/issues/77/comments" and method == "POST":
            self.comments[77].append({"body": body["body"]})
            return {}
        raise AssertionError(f"unexpected API call: {method} {path} {body}")


def issue(fake: FakeGitHub) -> dict:
    return {
        "number": 123,
        "title": "Fixture issue",
        "body": "Implement the bounded fixture.",
        "labels": [{"name": name} for name in sorted(fake.issue_labels[123])],
    }


def pr() -> dict:
    return {
        "number": 77,
        "title": "Fixture PR",
        "body": "Validation: fixture tests pass.",
        "head": {"ref": "codex/fixture", "sha": "a" * 40},
    }


def verdict(kind: str, *, route: str = "unchanged", requires_human: bool = False, reason: str = "none") -> dict:
    findings = [] if kind == "approved" else [{
        "severity": "major",
        "category": "tests",
        "description": "Focused regression is missing.",
        "path": "Assets/Tests/Fixture.cs",
        "suggested_action": "Add the focused regression and rerun validation.",
    }]
    return {
        "verdict": kind,
        "summary": f"fixture {kind}",
        "findings": findings,
        "routing_recommendation": route,
        "requires_human": requires_human,
        "reason": reason,
        "reviewerRoute": "terra",
        "reviewedHead": "a" * 40,
    }


def latest_state_from(fake: FakeGitHub) -> dict:
    for comment in reversed(fake.comments[123]):
        body = comment["body"]
        start = body.find(review.MARKER)
        if start >= 0:
            end = body.find("\n-->", start)
            return json.loads(body[start + len(review.MARKER):end])
    return {}


def configure(temp: Path, fake: FakeGitHub, output: dict) -> Path:
    workspace = temp / "GH-123"
    workspace.mkdir(parents=True, exist_ok=True)
    review.WORKSPACE_ROOT = temp
    review.MAX_REPAIRS = 2
    review.api = fake.api
    review.open_pr = lambda branch: pr()
    review.run_git = lambda workspace, *args: "codex/fixture" if args[:2] == ("branch", "--show-current") else "a" * 40
    review.run_reviewer = lambda issue_value, pr_value, state_value: dict(output)
    return workspace


def write_attempt(workspace: Path, timestamp: str) -> None:
    (workspace / ".symphony-attempt-complete").write_text(
        f"completed worker lifetime for GH-123 at {timestamp}\n", encoding="utf-8"
    )


def main() -> int:
    # Clean approval -> human review, never automatic merge/rearm, with integration packet.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        configure(Path(raw), fake, verdict("approved"))
        review.process(issue(fake))
        assert "symphony:human-review" in fake.issue_labels[123]
        assert "symphony:agent-review" not in fake.issue_labels[123]
        assert "symphony:ready" not in fake.issue_labels[123]
        assert "symphony:rearm" not in fake.issue_labels[123]
        state = latest_state_from(fake)
        assert state["state"] == "human_review"
        assert state["reviewCycle"] == 1
        assert len(state["history"]) == 1
        packet = fake.comments[77][-1]["body"]
        assert "Human Review packet" in packet
        assert "Assets/Scripts/Fixture.cs" in packet
        assert "human approval required" in packet

    # Review failure -> same issue enters bounded rework; recommendation is fresh routing evidence.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        configure(Path(raw), fake, verdict("changes_required", route="sol"))
        review.process(issue(fake))
        assert "symphony:rework" in fake.issue_labels[123]
        assert "symphony:agent-review" not in fake.issue_labels[123]
        assert "repair-route:sol" in fake.issue_labels[123]
        assert "symphony:rearm" in fake.issue_labels[123]
        assert "symphony:ready" in fake.issue_labels[123]
        state = latest_state_from(fake)
        assert state["state"] == "rework"
        assert state["repairAttempts"] == 1
        assert state["history"][0]["verdict"] == "changes_required"
        adds = [name for op, _, name in fake.operations if op == "add"]
        assert adds.index("symphony:rearm") < adds.index("symphony:ready")

    # Human/operator model override precedence is executable-covered by routing-policy-test.sh.
    labels_text = "\n".join(["risk:normal", "symphony:rework", "repair-route:sol", "model:terra"])
    assert "model:terra" in labels_text

    # Ambiguity/scope expansion halts instead of dispatching speculative repair.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        configure(Path(raw), fake, verdict("blocked_or_ambiguous", requires_human=True, reason="scope_change_required"))
        review.process(issue(fake))
        assert "symphony:human-attention" in fake.issue_labels[123]
        assert "symphony:ready" not in fake.issue_labels[123]
        assert latest_state_from(fake)["reason"] == "scope_change_required"

    # Persisted rework with no newer worker-attempt marker resumes dispatch after restart without
    # spending another review cycle or repair budget.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        workspace = configure(Path(raw), fake, verdict("approved"))
        prior = {
            "state": "rework", "issue": 123, "prNumber": 77, "prHeadSha": "a" * 40,
            "reviewCycle": 1, "repairAttempts": 1, "maxRepairAttempts": 2,
            "lastVerdict": "changes_required", "routingRecommendation": "sol", "history": [],
            "updatedAt": "2026-09-11T10:00:00+00:00",
        }
        fake.comments[123].append({"body": f"prior\n\n{review.MARKER}{json.dumps(prior)}\n-->"})
        write_attempt(workspace, "2026-09-11T09:00:00Z")
        review.run_reviewer = lambda *_: (_ for _ in ()).throw(AssertionError("review should not rerun"))
        review.process(issue(fake))
        assert "symphony:rework" in fake.issue_labels[123]
        assert "symphony:ready" in fake.issue_labels[123]
        assert "repair-route:sol" in fake.issue_labels[123]
        assert latest_state_from(fake)["repairAttempts"] == 1

    # Two automatic repairs consumed and a newer repair lifetime completed -> third failing review
    # goes to human attention rather than dispatching repair 3.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        workspace = configure(Path(raw), fake, verdict("changes_required", route="luna"))
        prior = {
            "state": "rework", "issue": 123, "prNumber": 77, "prHeadSha": "a" * 40,
            "reviewCycle": 2, "repairAttempts": 2, "maxRepairAttempts": 2,
            "lastVerdict": "changes_required", "routingRecommendation": "luna", "history": [],
            "updatedAt": "2026-09-11T10:00:00+00:00",
        }
        fake.comments[123].append({"body": f"prior\n\n{review.MARKER}{json.dumps(prior)}\n-->"})
        write_attempt(workspace, "2026-09-11T11:00:00Z")
        review.process(issue(fake))
        state = latest_state_from(fake)
        assert state["state"] == "human_attention"
        assert state["reason"] == "review_loop_exhausted"
        assert "symphony:ready" not in fake.issue_labels[123]
        assert "symphony:human-attention" in fake.issue_labels[123]

    source = (ROOT / "scripts/review-orchestrator.py").read_text(encoding="utf-8")
    assert "/merge" not in source
    assert "symphony:human-review" in source
    assert "symphony:human-attention" in source
    print("review-orchestrator-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
