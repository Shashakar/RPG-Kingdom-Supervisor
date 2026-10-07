#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
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
        "human_rework_assessment": None,
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

    # A first-class human_rework state keeps the rejected PR generation in rework without
    # consuming automated repair budget or silently restoring human-review approval.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        workspace = configure(Path(raw), fake, verdict("approved"))
        prior = {
            "state": "human_rework", "issue": 123, "prNumber": 77, "prHeadSha": "a" * 40,
            "reviewCycle": 1, "repairAttempts": 0, "maxRepairAttempts": 2,
            "lastVerdict": "human_changes_required", "reason": "human_rework",
            "humanRework": {
                "baselineHead": "a" * 40, "currentHead": "a" * 40,
                "directives": ["Implement the runtime production repair."], "prCommentId": 9001,
            },
            "history": [], "updatedAt": "2026-10-07T02:00:00+00:00",
        }
        fake.comments[123].append({"body": "prior\n\n" + review.MARKER + json.dumps(prior) + "\n-->"})
        write_attempt(workspace, "2026-10-07T01:00:00Z")
        review.run_reviewer = lambda *_: (_ for _ in ()).throw(AssertionError("rejected head must not be reviewed"))
        review.process(issue(fake))
        assert "symphony:rework" in fake.issue_labels[123]
        assert "symphony:human-review" not in fake.issue_labels[123]
        state = latest_state_from(fake)
        assert state["state"] == "human_rework"
        assert state["repairAttempts"] == 0
        assert state["humanRework"]["baselineHead"] == "a" * 40

    # Human-requested rework cannot satisfy a new generation by completing another worker
    # lifetime on the exact head recorded at the human gate. The durable prHeadSha is the
    # generation baseline even if review history's latest head differs.
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
        review.run_reviewer = lambda *_: (_ for _ in ()).throw(
            AssertionError("human-rejected baseline head must not be reviewed again")
        )
        review.process(issue(fake))
        state = latest_state_from(fake)
        assert state["state"] == "human_review"
        assert state["reviewCycle"] == 1
        assert len(state["history"]) == 1

    # Human rework directives added after a human gate become durable reviewer context. The
    # reviewer must assess the delta from the rejected baseline rather than merely the whole PR.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        workspace = configure(Path(raw), fake, verdict("approved"))
        old_head = "a" * 40
        new_head = "b" * 40
        prior = {
            "state": "human_review", "issue": 123, "prNumber": 77, "prHeadSha": old_head,
            "reviewCycle": 1, "repairAttempts": 0, "maxRepairAttempts": 2,
            "lastVerdict": "approved", "history": [],
            "updatedAt": "2026-10-06T20:00:00+00:00",
        }
        fake.comments[123].append({
            "body": "## Human rework\nImplement the runtime architecture change: replace two physical loot sources with one composed corpse source; test-only changes do not satisfy this rework.",
            "created_at": "2026-10-06T20:05:00Z",
        })
        fake.comments[123].append({
            "body": "Implementation/repair handoff completed for PR #77.",
            "created_at": "2026-10-06T20:10:00Z",
        })
        context = review.human_rework_context(123, prior, new_head)
        assert context is not None
        assert context["baselineHead"] == old_head
        assert context["currentHead"] == new_head
        assert context["directives"] == [
            "## Human rework\nImplement the runtime architecture change: replace two physical loot sources with one composed corpse source; test-only changes do not satisfy this rework."
        ]
        state = {
            "reviewCycle": 2, "repairAttempts": 0, "maxRepairAttempts": 2,
            "humanRework": context,
        }
        prompt = review.build_prompt(issue(fake), pr(), state, "sol")
        assert f"git diff {old_head}...HEAD" not in prompt  # reviewer receives the baseline as data, not shell interpolation
        assert old_head in prompt
        assert "Implement the runtime architecture change: replace two physical loot sources with one composed corpse source; test-only changes do not satisfy this rework." in prompt
        assert "Do not approve unless the delta" in prompt
        assert "test-only edits are not evidence" in prompt
        assert "Always populate human_rework_assessment" in prompt

        # Host acceptance is independent of reviewer prompt obedience. Reproduce GH-242 cycle 9:
        # a human runtime/production directive followed by a test-only continuation cannot pass
        # merely because the reviewer returns approved.
        test_only = verdict("approved")
        test_only["reviewedHead"] = new_head
        test_only["human_rework_assessment"] = {
            "baseline_head": old_head,
            "current_head": new_head,
            "directives_satisfied": True,
            "evidence_paths": ["Assets/RPGKingdom/Tests/PlayMode/WorldLoot/Runtime/WorldLootPlayModeTests.cs"],
        }
        original_run_git = review.run_git
        review.run_git = lambda _workspace, *args: (
            "Assets/RPGKingdom/Tests/PlayMode/WorldLoot/Runtime/WorldLootPlayModeTests.cs"
            if args[:2] == ("diff", "--name-only") else original_run_git(_workspace, *args)
        )
        try:
            rejected = review.enforce_human_rework_acceptance(workspace, state, test_only)
        finally:
            review.run_git = original_run_git
        assert rejected["verdict"] == "changes_required"
        assert "only tests/docs/non-implementation changes" in rejected["summary"]

        runtime_change = verdict("approved")
        runtime_change["reviewedHead"] = new_head
        runtime_change["human_rework_assessment"] = {
            "baseline_head": old_head,
            "current_head": new_head,
            "directives_satisfied": True,
            "evidence_paths": ["Assets/RPGKingdom/Runtime/WorldLoot/Unity/WorldLootRoot.cs"],
        }
        review.run_git = lambda _workspace, *args: (
            "Assets/RPGKingdom/Runtime/WorldLoot/Unity/WorldLootRoot.cs"
            if args[:2] == ("diff", "--name-only") else original_run_git(_workspace, *args)
        )
        try:
            accepted = review.enforce_human_rework_acceptance(workspace, state, runtime_change)
        finally:
            review.run_git = original_run_git
        assert accepted["verdict"] == "approved"

        # Explicit multi-category human directives are independently host-gated.
        multi_context = {
            "baselineHead": old_head,
            "currentHead": new_head,
            "directives": [
                "Modify the production scene placement in VerticalSlice.unity. "
                "Repair the staged Journey / Objective / Soul Guidance progression. "
                "Update focused tests and test assertions proving the order."
            ],
        }
        multi_state = {
            "reviewCycle": 2, "repairAttempts": 0, "maxRepairAttempts": 2,
            "humanRework": multi_context,
        }

        def assess(paths):
            value = verdict("approved")
            value["reviewedHead"] = new_head
            value["human_rework_assessment"] = {
                "baseline_head": old_head,
                "current_head": new_head,
                "directives_satisfied": True,
                "evidence_paths": paths,
            }
            review.run_git = lambda _workspace, *args: (
                "\n".join(paths) if args[:2] == ("diff", "--name-only") else original_run_git(_workspace, *args)
            )
            try:
                return review.enforce_human_rework_acceptance(workspace, multi_state, value)
            finally:
                review.run_git = original_run_git

        assert assess(["docs/SAVE_SYSTEM.md"])["verdict"] == "changes_required"

        scene_only = assess(["Assets/RPGKingdom/Scenes/VerticalSlice.unity"])
        assert scene_only["verdict"] == "changes_required"
        assert "journey, tests" in scene_only["summary"]

        scene_and_journey = assess([
            "Assets/RPGKingdom/Scenes/VerticalSlice.unity",
            "Assets/RPGKingdom/Runtime/Journey/VerticalSliceJourney.cs",
        ])
        assert scene_and_journey["verdict"] == "changes_required"
        assert "tests" in scene_and_journey["summary"]

        complete_evidence = assess([
            "Assets/RPGKingdom/Scenes/VerticalSlice.unity",
            "Assets/RPGKingdom/Runtime/Journey/VerticalSliceJourney.cs",
            "Assets/RPGKingdom/Tests/PlayMode/E2E/VerticalSlice/VerticalSliceJourneyPlayModeTests.cs",
        ])
        assert complete_evidence["verdict"] == "approved"

        # Directives without explicit artifact categories retain generic implementation gating.
        generic_state = {
            "reviewCycle": 2, "repairAttempts": 0, "maxRepairAttempts": 2,
            "humanRework": {
                "baselineHead": old_head,
                "currentHead": new_head,
                "directives": ["Implement the production runtime repair."],
            },
        }
        generic = verdict("approved")
        generic["reviewedHead"] = new_head
        generic["human_rework_assessment"] = {
            "baseline_head": old_head,
            "current_head": new_head,
            "directives_satisfied": True,
            "evidence_paths": ["Assets/RPGKingdom/Runtime/WorldLoot/Unity/WorldLootRoot.cs"],
        }
        review.run_git = lambda _workspace, *args: (
            "Assets/RPGKingdom/Runtime/WorldLoot/Unity/WorldLootRoot.cs"
            if args[:2] == ("diff", "--name-only") else original_run_git(_workspace, *args)
        )
        try:
            assert review.enforce_human_rework_acceptance(workspace, generic_state, generic)["verdict"] == "approved"
        finally:
            review.run_git = original_run_git

    # Once the PR advances beyond the human-review baseline, that rework generation is eligible
    # for a fresh automated review and the cycle advances normally.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        workspace = configure(Path(raw), fake, verdict("approved"))
        old_head = "a" * 40
        new_head = "b" * 40
        prior = {
            "state": "human_review", "issue": 123, "prNumber": 77, "prHeadSha": old_head,
            "reviewCycle": 1, "repairAttempts": 0, "maxRepairAttempts": 2,
            "lastVerdict": "approved", "history": [{
                "cycle": 1, "head": old_head, "verdict": "approved", "summary": "prior approval",
                "findings": [], "routingRecommendation": "unchanged", "reviewerRoute": "sol", "reason": "none"
            }],
            "updatedAt": "2026-09-11T10:00:00+00:00",
        }
        fake.comments[123].append({"body": f"prior\n\n{review.MARKER}{json.dumps(prior)}\n-->"})
        write_attempt(workspace, "2026-09-11T11:00:00Z")
        advanced_pr = pr()
        advanced_pr["head"]["sha"] = new_head
        review.open_pr = lambda branch: advanced_pr
        review.run_git = lambda workspace, *args: (
            "codex/fixture" if args[:2] == ("branch", "--show-current") else new_head
        )
        advanced_verdict = verdict("approved")
        advanced_verdict["reviewedHead"] = new_head
        review.run_reviewer = lambda *_: dict(advanced_verdict)
        review.process(issue(fake))
        state = latest_state_from(fake)
        assert state["state"] == "human_review"
        assert state["prHeadSha"] == new_head
        assert state["reviewCycle"] == 2
        assert len(state["history"]) == 2

    # Inherited human-rework context keeps the original rejected baseline but updates currentHead
    # to each newly reviewed PR generation. This prevents a valid approval after a second
    # human-authorized continuation from being rejected against stale continuation metadata.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        workspace = configure(Path(raw), fake, verdict("approved"))
        baseline = "5" * 40
        prior_head = "6" * 40
        current_head = "7" * 40
        prior = {
            "state": "agent_review", "issue": 123, "prNumber": 77, "prHeadSha": prior_head,
            "reviewCycle": 2, "repairAttempts": 2, "maxRepairAttempts": 2,
            "lastVerdict": "changes_required", "history": [{
                "cycle": 2, "head": prior_head, "verdict": "changes_required",
                "summary": "targeted continuation required", "findings": [],
                "routingRecommendation": "luna", "reviewerRoute": "sol", "reason": "none",
            }],
            "humanRework": {
                "baselineHead": baseline,
                "currentHead": prior_head,
                "directives": ["Repair the production runtime interaction path."],
            },
            "updatedAt": "2026-10-07T05:00:00+00:00",
        }
        fake.comments[123].append({"body": f"prior\n\n{review.MARKER}{json.dumps(prior)}\n-->"})
        write_attempt(workspace, "2026-10-07T05:05:00Z")
        advanced_pr = pr()
        advanced_pr["head"]["sha"] = current_head
        review.open_pr = lambda branch: advanced_pr
        review.run_git = lambda _workspace, *args: (
            "codex/fixture" if args[:2] == ("branch", "--show-current")
            else "Assets/RPGKingdom/Runtime/WorldLoot/Unity/WorldLootRoot.cs"
        )
        approved = verdict("approved")
        approved["reviewedHead"] = current_head
        approved["human_rework_assessment"] = {
            "baseline_head": baseline,
            "current_head": current_head,
            "directives_satisfied": True,
            "evidence_paths": ["Assets/RPGKingdom/Runtime/WorldLoot/Unity/WorldLootRoot.cs"],
        }
        seen_state: dict = {}
        def capture_reviewer(_issue, _pr, state_value):
            seen_state.update(state_value)
            return dict(approved)
        review.run_reviewer = capture_reviewer
        review.process(issue(fake))
        state = latest_state_from(fake)
        assert seen_state["humanRework"]["baselineHead"] == baseline
        assert seen_state["humanRework"]["currentHead"] == current_head
        assert state["humanRework"]["baselineHead"] == baseline
        assert state["humanRework"]["currentHead"] == current_head
        assert state["state"] == "human_review"

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

    # A prior retrospective visual false-positive may leave the issue at human attention.
    # A new visual request can recheck the same PR head without rearming implementation.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        fake.issue_labels[123] = {"risk:normal", "symphony:human-attention"}
        workspace = configure(Path(raw), fake, verdict("approved"))
        capture = workspace / "Logs" / "SymphonyUnity" / "capture" / "scene.png"
        capture.parent.mkdir(parents=True, exist_ok=True)
        capture.write_bytes(b"png")
        prior = {
            "state": "human_attention", "issue": 123, "prNumber": 77, "prHeadSha": "a" * 40,
            "reviewCycle": 2, "repairAttempts": 0, "maxRepairAttempts": 2,
            "lastVerdict": "changes_required", "routingRecommendation": "sol",
            "history": [
                {"cycle": 1, "head": "a" * 40, "verdict": "approved", "summary": "technical approval",
                 "findings": [], "routingRecommendation": "unchanged", "reviewerRoute": "sol", "reason": "none"},
                {"cycle": 2, "head": "a" * 40, "verdict": "changes_required", "summary": "bad capture",
                 "findings": [], "routingRecommendation": "sol", "reviewerRoute": "sol", "reason": "none"},
            ],
            "updatedAt": "2026-10-02T16:54:14+00:00",
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
        assert state["reviewCycle"] == 3
        assert state["repairAttempts"] == 0
        assert "symphony:human-review" in fake.issue_labels[123]
        assert "symphony:human-attention" not in fake.issue_labels[123]
        assert "symphony:ready" not in fake.issue_labels[123]
        assert "symphony:rearm" not in fake.issue_labels[123]

    # Retrospective visual review may continue after the PR head advances by descendant history
    # (for example, merging current main into the reviewed branch), provided the workspace and
    # fresh captures are at the new head.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        fake.issue_labels[123] = {"risk:normal", "symphony:human-attention"}
        workspace = configure(Path(raw), fake, verdict("approved"))
        capture = workspace / "Logs" / "SymphonyUnity" / "capture" / "scene.png"
        capture.parent.mkdir(parents=True, exist_ok=True)
        capture.write_bytes(b"png")
        old_head = "a" * 40
        new_head = "b" * 40
        prior = {
            "state": "human_attention", "issue": 123, "prNumber": 77, "prHeadSha": old_head,
            "reviewCycle": 2, "repairAttempts": 0, "maxRepairAttempts": 2,
            "lastVerdict": "changes_required", "routingRecommendation": "sol",
            "history": [
                {"cycle": 1, "head": old_head, "verdict": "approved", "summary": "technical approval",
                 "findings": [], "routingRecommendation": "unchanged", "reviewerRoute": "sol", "reason": "none"},
                {"cycle": 2, "head": old_head, "verdict": "changes_required", "summary": "bad capture",
                 "findings": [], "routingRecommendation": "sol", "reviewerRoute": "sol", "reason": "none"},
            ],
            "updatedAt": "2026-10-02T16:54:14+00:00",
        }
        fake.comments[123].append({"body": f"prior\n\n{review.MARKER}{json.dumps(prior)}\n-->"})
        original_api = review.api
        original_run_git = review.run_git
        original_fresh = review.fresh_visual_capture_images

        def advanced_api(method, path, body=None):
            if method == "GET" and path == "/pulls/77":
                value = pr()
                value["head"]["sha"] = new_head
                return value
            return original_api(method, path, body)

        def advanced_git(_workspace, *args):
            if args[:2] == ("branch", "--show-current"):
                return "codex/fixture"
            if args[:2] == ("rev-parse", "HEAD"):
                return new_head
            if args[:2] == ("merge-base", "--is-ancestor"):
                assert args[2:] == (old_head, new_head)
                return ""
            return original_run_git(_workspace, *args)

        review.api = advanced_api
        review.run_git = advanced_git
        review.fresh_visual_capture_images = lambda _: [capture]
        try:
            review.retrospective_visual_review(123, 77)
        finally:
            review.api = original_api
            review.run_git = original_run_git
            review.fresh_visual_capture_images = original_fresh
        state = latest_state_from(fake)
        assert state["state"] == "human_review"
        assert state["prHeadSha"] == new_head
        assert state["reviewCycle"] == 3

    # Divergent/non-fast-forward PR head movement remains blocked.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        fake.issue_labels[123] = {"risk:normal", "symphony:human-attention"}
        workspace = configure(Path(raw), fake, verdict("approved"))
        capture = workspace / "Logs" / "SymphonyUnity" / "capture" / "scene.png"
        capture.parent.mkdir(parents=True, exist_ok=True)
        capture.write_bytes(b"png")
        old_head = "a" * 40
        new_head = "b" * 40
        prior = {
            "state": "human_attention", "issue": 123, "prNumber": 77, "prHeadSha": old_head,
            "reviewCycle": 2, "repairAttempts": 0, "maxRepairAttempts": 2,
            "lastVerdict": "changes_required", "routingRecommendation": "sol",
            "history": [], "updatedAt": "2026-10-02T16:54:14+00:00",
        }
        fake.comments[123].append({"body": f"prior\n\n{review.MARKER}{json.dumps(prior)}\n-->"})
        original_api = review.api
        original_run_git = review.run_git
        original_fresh = review.fresh_visual_capture_images

        def divergent_api(method, path, body=None):
            if method == "GET" and path == "/pulls/77":
                value = pr()
                value["head"]["sha"] = new_head
                return value
            return original_api(method, path, body)

        def divergent_git(_workspace, *args):
            if args[:2] == ("branch", "--show-current"):
                return "codex/fixture"
            if args[:2] == ("rev-parse", "HEAD"):
                return new_head
            if args[:2] == ("merge-base", "--is-ancestor"):
                raise review.subprocess.CalledProcessError(1, ["git", *args])
            return original_run_git(_workspace, *args)

        review.api = divergent_api
        review.run_git = divergent_git
        review.fresh_visual_capture_images = lambda _: [capture]
        try:
            try:
                review.retrospective_visual_review(123, 77)
            except RuntimeError as exc:
                assert "non-fast-forward" in str(exc)
            else:
                raise AssertionError("divergent PR head should have been rejected")
        finally:
            review.api = original_api
            review.run_git = original_run_git
            review.fresh_visual_capture_images = original_fresh

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

    # Visual review active-operation state is durable, archived on terminal state, and stale
    # owner recovery prevents a crashed review from remaining active forever.
    with tempfile.TemporaryDirectory() as raw:
        original_state_root = review.STATE_ROOT
        review.STATE_ROOT = Path(raw)
        try:
            started = review.start_visual_review_status(
                123,
                77,
                "Assets/RPGKingdom/Scenes/PlaytestScene.unity",
                {"views": [{"name": "overview"}, {"name": "exit"}]},
                9000,
            )
            assert started["state"] == "active"
            assert started["totalViews"] == 2
            assert review.visual_review_status_path(123).is_file()

            review.update_visual_review_status(
                123,
                phase="capturing",
                pid=os.getpid(),
                currentViewIndex=1,
                currentViewName="overview",
                completedViews=0,
                summary="Capturing view 1 of 2: overview",
            )
            active = json.loads(review.visual_review_status_path(123).read_text(encoding="utf-8"))
            assert active["phase"] == "capturing"
            assert active["currentViewName"] == "overview"

            archived = review.archive_visual_review_status(123, "completed", "done")
            assert archived and archived["state"] == "completed"
            assert not review.visual_review_status_path(123).exists()
            assert list((Path(raw) / "visual-reviews" / "history").glob("GH-123-*-completed.json"))

            stale = review.start_visual_review_status(
                123, 77, "Assets/RPGKingdom/Scenes/PlaytestScene.unity", None, 9001
            )
            review.update_visual_review_status(
                123,
                pid=99999999,
                updatedAt="2000-01-01T00:00:00+00:00",
                phase="capturing",
            )
            # update_visual_review_status refreshes updatedAt; force an old timestamp for recovery.
            stale_path = review.visual_review_status_path(123)
            stale_payload = json.loads(stale_path.read_text(encoding="utf-8"))
            stale_payload["pid"] = 99999999
            stale_payload["updatedAt"] = "2000-01-01T00:00:00+00:00"
            review._atomic_json(stale_path, stale_payload)
            review.recover_stale_visual_review_statuses(max_age_seconds=1)
            assert not stale_path.exists()
            assert list((Path(raw) / "visual-reviews" / "history").glob("GH-123-*-stale.json"))
        finally:
            review.STATE_ROOT = original_state_root

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
        assert calls[0][0] == "bash"
        assert calls[0][1].endswith("scripts/visual-review-pr.sh")
        assert calls[0][2:] == [
            "--issue", "123", "--pr", "77",
            "--scene", "Assets/RPGKingdom/Scenes/PlaytestScene.unity",
        ]
        assert "symphony:visual-review" not in fake.issue_labels[123]
        assert "symphony:human-review" in fake.issue_labels[123]
        assert "symphony:ready" not in fake.issue_labels[123]

    # A visual-review execution failure writes a durable failure receipt instead of vanishing
    # into the local service log, and the same comment request cannot replay.
    with tempfile.TemporaryDirectory() as raw:
        fake = FakeGitHub()
        fake.issue_labels[123] = {"risk:normal", "symphony:human-review"}
        configure(Path(raw), fake, verdict("approved"))
        visual_issue = issue(fake)
        fake.comments[123].append({
            "id": 9101,
            "body": review.VISUAL_REVIEW_REQUEST_MARKER + "\nRun visual review.",
        })
        original_visual_requests = review.visual_review_requests
        original_process_visual = review.process_visual_review_request
        original_reviewable = review.reviewable_issues
        review.visual_review_requests = lambda: [visual_issue]
        review.process_visual_review_request = lambda _: (_ for _ in ()).throw(RuntimeError("capture exploded"))
        review.reviewable_issues = lambda: []
        try:
            review.once()
        finally:
            review.visual_review_requests = original_visual_requests
            review.process_visual_review_request = original_process_visual
            review.reviewable_issues = original_reviewable
        assert review.pending_visual_review_comment(123) is None
        assert any("capture exploded" in item["body"] for item in fake.comments[123])
        assert any(f"{review.VISUAL_REVIEW_COMPLETE_PREFIX}9101 -->" in item["body"] for item in fake.comments[123])
        assert "symphony:ready" not in fake.issue_labels[123]
        assert "symphony:rearm" not in fake.issue_labels[123]

    # Once a diagnostics-capable capture exists for the current head, legacy pre-diagnostics
    # captures are excluded so known-bad harness output cannot contaminate the new review.
    with tempfile.TemporaryDirectory() as raw:
        workspace = Path(raw) / "GH-123"
        unity_root = workspace / "Logs" / "SymphonyUnity"
        legacy_dir = unity_root / "legacy"
        diagnostic_dir = unity_root / "diagnostic"
        legacy_dir.mkdir(parents=True)
        diagnostic_dir.mkdir(parents=True)
        (legacy_dir / "scene.png").write_bytes(b"legacy")
        (legacy_dir / "summary.json").write_text(
            json.dumps({"result": "Captured", "image": "scene.png"}), encoding="utf-8"
        )
        (diagnostic_dir / "scene.png").write_bytes(b"diagnostic")
        (diagnostic_dir / "visual-diagnostics.json").write_text("{}", encoding="utf-8")
        (diagnostic_dir / "summary.json").write_text(
            json.dumps({
                "result": "Captured",
                "image": "scene.png",
                "diagnostics": "visual-diagnostics.json",
            }),
            encoding="utf-8",
        )
        original_run_git = review.run_git
        review.run_git = lambda *_: "0"
        try:
            selected = review.fresh_visual_capture_images(workspace)
        finally:
            review.run_git = original_run_git
        assert selected == [diagnostic_dir / "scene.png"]

    # Visual review requirements support up to four named temporary camera poses.
    profile_issue = {
        "body": """Fixture.
<!-- symphony-visual-review-requirements
{
  "views": [
    {
      "name": "settlement-overview",
      "camera": "ThirdPersonCamera",
      "position": [10, 7, -4],
      "lookAt": "Environment/BlockedExit",
      "fov": 55
    },
    {
      "name": "exit-approach",
      "camera": "ThirdPersonCamera",
      "position": [4, 2, 3],
      "rotation": [8, 125, 0]
    }
  ]
}
-->
"""
    }
    profile = review.visual_review_profile(profile_issue)
    assert profile is not None
    assert len(profile["views"]) == 2
    assert profile["views"][0]["name"] == "settlement-overview"
    assert profile["views"][0]["lookAt"] == "Environment/BlockedExit"
    assert profile["views"][1]["rotation"] == [8, 125, 0]

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
    prompt = review.build_prompt(issue(FakeGitHub()), pr(), {
        "reviewCycle": 2, "repairAttempts": 0, "maxRepairAttempts": 2
    }, "sol")
    assert "Magenta/pink pixels alone do not prove" in prompt
    assert "visual-diagnostics.json" in prompt
    assert "reason=insufficient_evidence" in prompt
    assert "/merge" not in source
    assert "symphony:human-review" in source
    assert "symphony:human-attention" in source
    print("review-orchestrator-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
