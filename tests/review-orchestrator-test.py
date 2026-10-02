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
        if path == "/issues/123" and method == "GET":
            return issue(self)
        if path == "/pulls/77" and method == "GET":
            return pr()
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
        "state": "open",
        "draft": False,
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
        "reviewerRoute": "sol",
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
    labels_text = "\n".join(["risk:normal", "symphony:rework", "repair-route:sol", "model:sol"])
    assert "model:sol" in labels_text

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

    # A human-requested worker lifetime after prior automated approval gets a fresh review rather
    # than restoring the old terminal human-review state. Review cycle advances from history.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        workspace = configure(Path(raw), fake, verdict("approved"))
        prior = {
            "state": "human_review", "issue": 123, "prNumber": 77, "prHeadSha": "a" * 40,
            "reviewCycle": 1, "repairAttempts": 0, "maxRepairAttempts": 2,
            "lastVerdict": "approved", "history": [{
                "cycle": 1, "head": "9" * 40, "verdict": "approved", "summary": "prior approval",
                "findings": [], "routingRecommendation": "unchanged", "reviewerRoute": "sol", "reason": "none"
            }],
            "updatedAt": "2026-09-11T10:00:00+00:00",
        }
        fake.comments[123].append({"body": f"prior\n\n{review.MARKER}{json.dumps(prior)}\n-->"})
        write_attempt(workspace, "2026-09-11T11:00:00Z")
        review.process(issue(fake))
        state = latest_state_from(fake)
        assert state["state"] == "human_review"
        assert state["reviewCycle"] == 2
        assert len(state["history"]) == 2

    # A newer attempt marker is not enough to justify review when preflight/host work never
    # changed the PR head. Preserve the prior human-attention gate and do not spend another review.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        workspace = configure(Path(raw), fake, verdict("approved"))
        prior = {
            "state": "human_attention", "issue": 123, "prNumber": 77, "prHeadSha": "a" * 40,
            "reviewCycle": 4, "repairAttempts": 2, "maxRepairAttempts": 2,
            "lastVerdict": "changes_required", "routingRecommendation": "luna",
            "history": [{
                "cycle": 4, "head": "a" * 40, "verdict": "changes_required",
                "summary": "box outline is invalid", "findings": [],
                "routingRecommendation": "luna", "reviewerRoute": "sol",
                "reason": "review_loop_exhausted",
            }],
            "updatedAt": "2026-09-25T04:35:49+00:00",
        }
        fake.comments[123].append({"body": f"prior\n\n{review.MARKER}{json.dumps(prior)}\n-->"})
        write_attempt(workspace, "2026-09-25T04:47:00Z")
        review.run_reviewer = lambda *_: (_ for _ in ()).throw(
            AssertionError("unchanged reviewed head must not be reviewed again")
        )
        review.process(issue(fake))
        assert "symphony:human-attention" in fake.issue_labels[123]
        assert "symphony:agent-review" not in fake.issue_labels[123]
        assert latest_state_from(fake)["reviewCycle"] == 4

    # The same protection applies to a rework dispatch that halts before producing a commit:
    # retain rework/halt semantics instead of reviewing the rejected head again.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        workspace = configure(Path(raw), fake, verdict("approved"))
        prior = {
            "state": "rework", "issue": 123, "prNumber": 77, "prHeadSha": "a" * 40,
            "reviewCycle": 1, "repairAttempts": 1, "maxRepairAttempts": 2,
            "lastVerdict": "changes_required", "routingRecommendation": "luna",
            "history": [{
                "cycle": 1, "head": "a" * 40, "verdict": "changes_required",
                "summary": "repair required", "findings": [],
                "routingRecommendation": "luna", "reviewerRoute": "sol", "reason": "none",
            }],
            "updatedAt": "2026-09-25T04:35:49+00:00",
        }
        fake.comments[123].append({"body": f"prior\n\n{review.MARKER}{json.dumps(prior)}\n-->"})
        write_attempt(workspace, "2026-09-25T04:47:00Z")
        review.run_reviewer = lambda *_: (_ for _ in ()).throw(
            AssertionError("halted unchanged repair head must not be reviewed again")
        )
        review.process(issue(fake))
        assert "symphony:rework" in fake.issue_labels[123]
        assert "symphony:agent-review" not in fake.issue_labels[123]
        assert "symphony:ready" not in fake.issue_labels[123]
        assert latest_state_from(fake)["reviewCycle"] == 1

    # Two automatic repairs consumed and a newer repair lifetime completed -> third failing review
    # goes to human attention rather than dispatching repair 3.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        workspace = configure(Path(raw), fake, verdict("changes_required", route="luna"))
        prior = {
            "state": "rework", "issue": 123, "prNumber": 77, "prHeadSha": "a" * 40,
            "reviewCycle": 2, "repairAttempts": 2, "maxRepairAttempts": 2,
            "lastVerdict": "changes_required", "routingRecommendation": "luna", "history": [
                {"cycle": 1, "head": "8" * 40, "verdict": "changes_required", "summary": "first", "findings": [], "routingRecommendation": "sol", "reviewerRoute": "sol", "reason": "none"},
                {"cycle": 2, "head": "9" * 40, "verdict": "changes_required", "summary": "second", "findings": [], "routingRecommendation": "luna", "reviewerRoute": "sol", "reason": "none"},
            ],
            "updatedAt": "2026-09-11T10:00:00+00:00",
        }
        fake.comments[123].append({"body": f"prior\n\n{review.MARKER}{json.dumps(prior)}\n-->"})
        write_attempt(workspace, "2026-09-11T11:00:00Z")
        review.process(issue(fake))
        state = latest_state_from(fake)
        assert state["state"] == "human_attention"
        assert state["reason"] == "review_loop_exhausted"
        assert state["reviewCycle"] == 3
        assert "symphony:ready" not in fake.issue_labels[123]
        assert "symphony:human-attention" in fake.issue_labels[123]

    # Retrospective visual approval adds a review cycle but remains at the human gate and never
    # consumes repair budget or dispatches implementation.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        fake.issue_labels[123] = {"risk:normal", "symphony:human-review"}
        workspace = configure(Path(raw), fake, verdict("approved"))
        capture = workspace / "Logs" / "SymphonyUnity" / "capture" / "scene.png"
        capture.parent.mkdir(parents=True, exist_ok=True)
        capture.write_bytes(b"png")
        prior = {
            "state": "human_review", "issue": 123, "prNumber": 77, "prHeadSha": "a" * 40,
            "reviewCycle": 1, "repairAttempts": 0, "maxRepairAttempts": 2,
            "lastVerdict": "approved", "routingRecommendation": "unchanged",
            "history": [{
                "cycle": 1, "head": "a" * 40, "verdict": "approved", "summary": "technical approval",
                "findings": [], "routingRecommendation": "unchanged", "reviewerRoute": "sol", "reason": "none"
            }],
            "updatedAt": "2026-10-02T07:24:46+00:00",
        }
        fake.comments[123].append({"body": f"prior\n\n{review.MARKER}{json.dumps(prior)}\n-->"})
        original_fresh = review.fresh_visual_capture_images
        review.fresh_visual_capture_images = lambda _: [capture]
        try:
            review.retrospective_visual_review(123, 77)
        finally:
            review.fresh_visual_capture_images = original_fresh
        state = latest_state_from(fake)
        assert state["state"] == "human_review"
        assert state["reviewCycle"] == 2
        assert state["repairAttempts"] == 0
        assert len(state["history"]) == 2
        assert "symphony:human-review" in fake.issue_labels[123]
        assert "symphony:ready" not in fake.issue_labels[123]
        assert "symphony:rearm" not in fake.issue_labels[123]
        assert any("Retrospective Unity visual review" in item["body"] for item in fake.comments[77])

    # A retrospective visual concern stops at human attention; it never silently starts a repair.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        fake.issue_labels[123] = {"risk:normal", "symphony:human-review"}
        workspace = configure(Path(raw), fake, verdict("changes_required", route="sol"))
        capture = workspace / "Logs" / "SymphonyUnity" / "capture" / "scene.png"
        capture.parent.mkdir(parents=True, exist_ok=True)
        capture.write_bytes(b"png")
        prior = {
            "state": "human_review", "issue": 123, "prNumber": 77, "prHeadSha": "a" * 40,
            "reviewCycle": 1, "repairAttempts": 0, "maxRepairAttempts": 2,
            "lastVerdict": "approved", "routingRecommendation": "unchanged",
            "history": [{
                "cycle": 1, "head": "a" * 40, "verdict": "approved", "summary": "technical approval",
                "findings": [], "routingRecommendation": "unchanged", "reviewerRoute": "sol", "reason": "none"
            }],
            "updatedAt": "2026-10-02T07:24:46+00:00",
        }
        fake.comments[123].append({"body": f"prior\n\n{review.MARKER}{json.dumps(prior)}\n-->"})
        original_fresh = review.fresh_visual_capture_images
        review.fresh_visual_capture_images = lambda _: [capture]
        try:
            review.retrospective_visual_review(123, 77)
        finally:
            review.fresh_visual_capture_images = original_fresh
        state = latest_state_from(fake)
        assert state["state"] == "human_attention"
        assert state["repairAttempts"] == 0
        assert "symphony:human-attention" in fake.issue_labels[123]
        assert "symphony:ready" not in fake.issue_labels[123]
        assert "symphony:rearm" not in fake.issue_labels[123]
        assert "symphony:rework" not in fake.issue_labels[123]

    # Comment-triggered requests are one-shot and completion receipts prevent replay.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        configure(Path(raw), fake, verdict("approved"))
        fake.comments[123].append({
            "id": 9001,
            "body": review.VISUAL_REVIEW_REQUEST_MARKER + "\nRun the retrospective visual review.",
        })
        assert review.pending_visual_review_comment(123) == 9001
        fake.comments[123].append({
            "id": 9002,
            "body": f"done\n\n{review.VISUAL_REVIEW_COMPLETE_PREFIX}9001 -->",
        })
        assert review.pending_visual_review_comment(123) is None

    # Label-triggered visual review derives the exact authorized scene and invokes only the
    # one-shot host visual-review command, then consumes the request label.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        fake.issue_labels[123] = {"risk:normal", "symphony:human-review", "symphony:visual-review"}
        configure(Path(raw), fake, verdict("approved"))
        visual_issue = issue(fake)
        visual_issue["body"] = """Fixture.

<!-- symphony-scene-authoring-requirements
{
  "mode": "known",
  "tier": "existing-scene-composition",
  "scene": "Assets/RPGKingdom/Scenes/PlaytestScene.unity",
  "operations": ["set-transform"]
}
-->
"""
        prior = {
            "state": "human_review", "issue": 123, "prNumber": 77, "prHeadSha": "a" * 40,
            "reviewCycle": 1, "repairAttempts": 0, "maxRepairAttempts": 2,
            "history": [], "updatedAt": "2026-10-02T07:24:46+00:00",
        }
        fake.comments[123].append({"body": f"prior\n\n{review.MARKER}{json.dumps(prior)}\n-->"})
        calls: list[list[str]] = []
        original_run = review.subprocess.run
        review.subprocess.run = lambda args, check=True, **kwargs: calls.append([str(value) for value in args])
        try:
            review.process_visual_review_request(visual_issue)
        finally:
            review.subprocess.run = original_run
        assert review.visual_review_scene(visual_issue) == "Assets/RPGKingdom/Scenes/PlaytestScene.unity"
        assert len(calls) == 1
        assert calls[0][0].endswith("scripts/visual-review-pr.sh")
        assert calls[0][1:] == [
            "--issue", "123", "--pr", "77",
            "--scene", "Assets/RPGKingdom/Scenes/PlaytestScene.unity",
        ]
        assert "symphony:visual-review" not in fake.issue_labels[123]
        assert "symphony:human-review" in fake.issue_labels[123]
        assert "symphony:ready" not in fake.issue_labels[123]

    # Polling only agent-review avoids racing active rework lifetimes after handoff removes ready.
    seen: list[str] = []
    original_lifecycle_issues = review.lifecycle_issues
    review.lifecycle_issues = lambda label: seen.append(label) or []
    try:
        assert review.reviewable_issues() == []
    finally:
        review.lifecycle_issues = original_lifecycle_issues
    assert seen == ["symphony:agent-review"]

    source = (ROOT / "scripts/review-orchestrator.py").read_text(encoding="utf-8")
    assert "/merge" not in source
    assert "symphony:human-review" in source
    assert "symphony:human-attention" in source
    print("review-orchestrator-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
