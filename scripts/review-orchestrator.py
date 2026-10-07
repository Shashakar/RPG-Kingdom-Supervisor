#!/usr/bin/env python3
"""GitHub-backed automated review/rework state machine for RPG Kingdom Supervisor."""
from __future__ import annotations

import json
import os
import fcntl
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

REPO = os.environ.get("RPGK_REPO", "Shashakar/RPG-Kingdom")
API_ROOT = os.environ.get("RPGK_GITHUB_API_ROOT", "https://api.github.com").rstrip("/")
TOKEN = os.environ.get("SYMPHONY_GITHUB_TOKEN", "")
SUPERVISOR_ROOT = Path(os.environ.get("RPGK_SUPERVISOR_ROOT", str(Path.home() / "src/RPG-Kingdom-Supervisor"))).expanduser()
WORKSPACE_ROOT = Path(os.environ.get("RPGK_SYMPHONY_WORKSPACE_ROOT", str(Path.home() / "code/rpg-kingdom-symphony-workspaces"))).expanduser()
STATE_ROOT = Path(os.environ.get("RPGK_SUPERVISOR_STATE_ROOT", str(Path.home() / ".local/state/rpg-kingdom-supervisor"))).expanduser()
MAX_REPAIRS = int(os.environ.get("RPGK_MAX_AUTOMATIC_REPAIRS", "2"))
POLL_SECONDS = float(os.environ.get("RPGK_REVIEW_POLL_SECONDS", "15"))
MARKER = "<!-- rpgk-review-state\n"
VISUAL_REVIEW_REQUEST_MARKER = "<!-- rpgk-visual-review-request -->"
VISUAL_REVIEW_COMPLETE_PREFIX = "<!-- rpgk-visual-review-complete:"
VISUAL_REVIEW_REQUIREMENTS_MARKER = "<!-- symphony-visual-review-requirements"


def api(method: str, path: str, body: Any | None = None) -> Any:
    if not TOKEN:
        raise RuntimeError("SYMPHONY_GITHUB_TOKEN is required")
    data = None if body is None else json.dumps(body).encode()
    req = request.Request(
        f"{API_ROOT}/repos/{REPO}{path}", data=data, method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except error.HTTPError as exc:
        if method == "DELETE" and exc.code == 404:
            return None
        raise RuntimeError(f"GitHub {method} {path} failed: {exc.code} {exc.read().decode(errors='replace')}") from exc


def labels(issue: dict[str, Any]) -> set[str]:
    return {str(item.get("name", "")) for item in issue.get("labels", []) if isinstance(item, dict)}


def add_labels(number: int, *names: str) -> None:
    wanted = [name for name in names if name]
    if wanted:
        api("POST", f"/issues/{number}/labels", {"labels": wanted})


def remove_label(number: int, name: str) -> None:
    api("DELETE", f"/issues/{number}/labels/{parse.quote(name, safe='')}")


def post_comment(number: int, text: str) -> None:
    api("POST", f"/issues/{number}/comments", {"body": text})


def record_visual_review_event(issue_number: int, stage: str, *, pr_number: int | None = None, summary: str | None = None) -> None:
    """Append a local cross-system activity event without making telemetry availability a control dependency."""
    path = STATE_ROOT / "telemetry" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "protocolVersion": 1,
        "eventType": "visual_review",
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "issue": issue_number,
        "identifier": f"GH-{issue_number}",
        "stage": stage,
        "prNumber": pr_number,
        "summary": summary,
    }
    try:
        lock_path = Path(str(path) + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
    except OSError:
        pass


def _visual_review_active_dir() -> Path:
    return STATE_ROOT / "visual-reviews" / "active"


def _visual_review_history_dir() -> Path:
    return STATE_ROOT / "visual-reviews" / "history"


def visual_review_status_path(issue_number: int) -> Path:
    return _visual_review_active_dir() / f"GH-{issue_number}.json"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.replace(temp, path)


def start_visual_review_status(
    issue_number: int,
    pr_number: int,
    scene: str,
    profile: dict[str, Any] | None,
    request_comment_id: int | None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    total_views = len(profile.get("views") or []) if isinstance(profile, dict) else 1
    payload = {
        "protocolVersion": 1,
        "kind": "visual_review",
        "state": "active",
        "phase": "detected",
        "issue": issue_number,
        "identifier": f"GH-{issue_number}",
        "prNumber": pr_number,
        "scene": scene,
        "requestCommentId": request_comment_id,
        "startedAt": now,
        "updatedAt": now,
        "pid": os.getpid(),
        "totalViews": max(1, total_views),
        "currentViewIndex": 0,
        "currentViewName": None,
        "completedViews": 0,
        "summary": "Retrospective visual-review request detected.",
    }
    _atomic_json(visual_review_status_path(issue_number), payload)
    return payload


def update_visual_review_status(issue_number: int, **changes: Any) -> dict[str, Any] | None:
    path = visual_review_status_path(issue_number)
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(current, dict):
        return None
    current.update(changes)
    current["updatedAt"] = datetime.now(timezone.utc).isoformat()
    _atomic_json(path, current)
    return current


def archive_visual_review_status(issue_number: int, outcome: str, summary: str | None = None) -> dict[str, Any] | None:
    path = visual_review_status_path(issue_number)
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(current, dict):
        return None
    now = datetime.now(timezone.utc).isoformat()
    current.update({
        "state": outcome,
        "phase": outcome,
        "updatedAt": now,
        "endedAt": now,
    })
    if summary:
        current["summary"] = summary
    history_dir = _visual_review_history_dir()
    history_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.replace(":", "").replace("-", "").replace("+00:00", "Z")
    history_path = history_dir / f"GH-{issue_number}-{stamp}-{outcome}.json"
    _atomic_json(history_path, current)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return current


def _pid_alive(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def recover_stale_visual_review_statuses(max_age_seconds: float = 60.0) -> None:
    now = datetime.now(timezone.utc)
    active_dir = _visual_review_active_dir()
    if not active_dir.is_dir():
        return
    for path in active_dir.glob("GH-*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or value.get("state") != "active":
            continue
        updated = parse_time(value.get("updatedAt"))
        age = (now - updated).total_seconds() if updated else max_age_seconds + 1
        if age < max_age_seconds or _pid_alive(value.get("pid")):
            continue
        try:
            issue_number = int(value.get("issue"))
        except (TypeError, ValueError):
            continue
        archived = archive_visual_review_status(
            issue_number,
            "stale",
            "Visual review owner process exited before the operation completed.",
        )
        if archived:
            record_visual_review_event(
                issue_number,
                "failed",
                pr_number=archived.get("prNumber"),
                summary="Visual review was recovered as stale after its owner process exited.",
            )


def latest_state(number: int) -> dict[str, Any]:
    page = 1
    newest: dict[str, Any] = {}
    while page <= 20:
        comments = api("GET", f"/issues/{number}/comments?per_page=100&page={page}") or []
        if not comments:
            break
        for comment in comments:
            body = str(comment.get("body") or "")
            start = body.find(MARKER)
            if start < 0:
                continue
            end = body.find("\n-->", start)
            if end < 0:
                continue
            try:
                value = json.loads(body[start + len(MARKER):end])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                newest = value
        if len(comments) < 100:
            break
        page += 1
    return newest


def human_rework_context(issue_number: int, prior: dict[str, Any], current_head: str) -> dict[str, Any] | None:
    """Capture human continuation requirements that were added after a durable human gate."""
    baseline = prior.get("prHeadSha")
    if prior.get("state") not in {"human_review", "human_attention"}:
        return None
    if not isinstance(baseline, str) or not baseline or baseline == current_head:
        return None
    gate_time = parse_time(prior.get("updatedAt"))
    directives: list[str] = []
    page = 1
    generated_prefixes = (
        "Implementation/repair handoff completed",
        "### Automated review cycle",
        "## Human Review packet",
    )
    while page <= 20:
        comments = api("GET", f"/issues/{issue_number}/comments?per_page=100&page={page}") or []
        if not comments:
            break
        for comment in comments:
            body = str(comment.get("body") or "").strip()
            created = parse_time(comment.get("created_at"))
            if not body or MARKER in body or body.startswith(generated_prefixes):
                continue
            if gate_time is not None and created is not None and created <= gate_time:
                continue
            directives.append(body)
        if len(comments) < 100:
            break
        page += 1
    if not directives:
        return None
    return {
        "baselineHead": baseline,
        "currentHead": current_head,
        "directives": directives,
    }


def human_rework_requires_implementation(context: dict[str, Any]) -> bool:
    """Conservatively detect human continuations that explicitly demand production/runtime change."""
    text = "\n".join(str(item) for item in context.get("directives") or []).lower()
    markers = (
        "runtime", "architecture", "production", "implementation", "implement ",
        "not test-only", "test-only", "docs-only", "player-facing",
    )
    return any(marker in text for marker in markers)


def enforce_human_rework_acceptance(
    workspace: Path, state: dict[str, Any], verdict: dict[str, Any]
) -> dict[str, Any]:
    """Fail closed when an approval does not prove the human-requested continuation.

    Prompt instructions remain useful reviewer context, but they are not a control boundary.
    The host independently verifies that an approval names the rejected baseline/current head
    and cites files that actually changed in that continuation generation. Explicit runtime /
    architecture / production rework also requires non-test, non-doc implementation evidence.
    """
    context = state.get("humanRework")
    if verdict.get("verdict") != "approved" or not isinstance(context, dict):
        return verdict

    assessment = verdict.get("human_rework_assessment")
    baseline = context.get("baselineHead")
    current = context.get("currentHead")
    failures: list[str] = []
    evidence: list[str] = []

    if not isinstance(assessment, dict):
        failures.append("reviewer omitted the required human rework assessment")
    else:
        if assessment.get("baseline_head") != baseline:
            failures.append("assessment baseline does not match the rejected human-review head")
        if assessment.get("current_head") != current:
            failures.append("assessment current head does not match the reviewed continuation head")
        if assessment.get("directives_satisfied") is not True:
            failures.append("reviewer did not affirm that every human directive is satisfied")
        raw_evidence = assessment.get("evidence_paths")
        if isinstance(raw_evidence, list):
            evidence = [str(path) for path in raw_evidence if isinstance(path, str) and path]
        if not evidence:
            failures.append("assessment supplied no concrete changed-file evidence")

    changed: set[str] = set()
    if isinstance(baseline, str) and baseline:
        try:
            changed = {
                line.strip()
                for line in run_git(workspace, "diff", "--name-only", f"{baseline}...HEAD").splitlines()
                if line.strip()
            }
        except subprocess.CalledProcessError:
            failures.append("host could not verify the baseline...HEAD continuation delta")

    missing = sorted(path for path in evidence if path not in changed)
    if missing:
        failures.append("assessment cites paths not changed since the rejected baseline: " + ", ".join(missing))

    if human_rework_requires_implementation(context):
        implementation_evidence = [
            path for path in evidence
            if path in changed
            and not path.startswith(("docs/", ".github/"))
            and "/Tests/" not in path
            and not path.startswith("tests/")
            and not path.endswith((".md", ".txt"))
        ]
        if not implementation_evidence:
            failures.append(
                "human directives require runtime/production implementation, but the continuation "
                "assessment cites only tests/docs/non-implementation changes"
            )

    if not failures:
        return verdict

    verdict = dict(verdict)
    verdict["verdict"] = "changes_required"
    verdict["requires_human"] = False
    verdict["reason"] = "none"
    verdict["routing_recommendation"] = "sol"
    verdict["summary"] = (
        "Reviewer approval was rejected by the host human-rework acceptance gate: "
        + "; ".join(failures)
    )
    findings = list(verdict.get("findings") or [])
    findings.append({
        "severity": "major",
        "category": "correctness",
        "description": verdict["summary"],
        "path": None,
        "suggested_action": (
            "Implement the human-requested continuation against the recorded baseline and have "
            "the next review cite changed implementation paths that prove each directive."
        ),
    })
    verdict["findings"] = findings
    return verdict


def persist_state(issue_number: int, state: dict[str, Any], human_text: str) -> None:
    state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":"))
    post_comment(issue_number, f"{human_text}\n\n{MARKER}{encoded}\n-->")


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def attempt_completed_at(workspace: Path) -> datetime | None:
    marker = workspace / ".symphony-attempt-complete"
    try:
        line = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    prefix = f"completed worker lifetime for {workspace.name} at "
    return parse_time(line[len(prefix):]) if line.startswith(prefix) else None


def repair_completed_since_state(workspace: Path, state: dict[str, Any]) -> bool:
    completed = attempt_completed_at(workspace)
    updated = parse_time(state.get("updatedAt"))
    return bool(completed and updated and completed > updated)


def head_already_reviewed(pr: dict[str, Any], state: dict[str, Any]) -> bool:
    history = state.get("history")
    if not isinstance(history, list) or not history:
        return False
    latest = history[-1]
    return isinstance(latest, dict) and latest.get("head") == pr.get("head", {}).get("sha")


def head_matches_recorded_generation(pr: dict[str, Any], state: dict[str, Any]) -> bool:
    """Return whether the PR is still at the head recorded for this durable state.

    Human review/attention is itself a generation boundary. Its recorded prHeadSha is
    authoritative even when review history contains a different latest head (for
    example after retrospective review or lifecycle reconciliation).
    """
    recorded = state.get("prHeadSha")
    current = pr.get("head", {}).get("sha")
    return isinstance(recorded, str) and bool(recorded) and recorded == current


def run_git(workspace: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(workspace), *args], check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return completed.stdout.strip()


def open_pr(branch: str) -> dict[str, Any] | None:
    owner = REPO.split("/", 1)[0]
    query = parse.urlencode({"state": "open", "head": f"{owner}:{branch}", "base": "main", "per_page": 10})
    values = api("GET", f"/pulls?{query}") or []
    return values[0] if values else None


def changed_files(pr_number: int) -> list[str]:
    values = api("GET", f"/pulls/{pr_number}/files?per_page=100") or []
    return [str(item.get("filename")) for item in values if item.get("filename")]


def reviewer_route(issue_labels: set[str]) -> tuple[str, str, str]:
    override = os.environ.get("RPGK_REVIEW_MODEL")
    if override:
        return override, os.environ.get("RPGK_REVIEW_EFFORT", "medium"), "override"
    if "risk:architecture" in issue_labels or "risk:end-to-end" in issue_labels:
        return "gpt-6-sol", "high", "sol"
    return "gpt-6-sol", "medium", "sol"


def build_prompt(issue: dict[str, Any], pr: dict[str, Any], state: dict[str, Any], route: str) -> str:
    issue_labels = sorted(labels(issue))
    return f"""You are the independent review worker for RPG Kingdom issue GH-{issue['number']} and PR #{pr['number']}.

You are a reviewer, not the implementation worker. Work read-only. Do not edit files, commit, push, modify GitHub, weaken acceptance criteria, or repair findings in this run.

Review cycle: {state['reviewCycle']}
Automatic repairs already consumed: {state['repairAttempts']} / {state['maxRepairAttempts']}
Reviewer route: {route}
PR head that must be reviewed: {pr['head']['sha']}

Issue title: {issue.get('title','')}
Issue labels: {issue_labels}
Issue description / approved scope:
{issue.get('body') or '(none)'}

Current PR title: {pr.get('title','')}
Current PR description:
{pr.get('body') or '(none)'}

Human-requested rework context:
{json.dumps(state.get('humanRework'), indent=2) if state.get('humanRework') else '(none)'}

Review requirements:
1. Read repository-root AGENTS.md and only the architecture/system docs it requires for the changed scope.
2. Inspect the actual current diff with `git diff origin/main...HEAD` and changed-file list. Repository state, not prior worker prose or hidden reasoning, is authoritative.
3. Inspect relevant tests and the latest Supervisor Unity artifacts under Logs/SymphonyUnity when validation is material.
4. Check correctness, architecture/system boundaries, regression risk, persistence/save contracts, tests, docs, and whether validation actually proves the changed behavior.
5. Do not manufacture blockers. Minor non-blocking suggestions may be findings, but `changes_required` means the PR should not enter human integration review yet.
6. If the correct fix requires material work outside the approved issue scope, choose `blocked_or_ambiguous`, set requires_human=true, and reason=scope_change_required rather than silently expanding scope.
7. If product/design intent is genuinely ambiguous, choose `blocked_or_ambiguous` and require human attention.
8. `routing_recommendation` is advisory for a repair task. Recommend the cheapest route likely to resolve the actual findings; use `unchanged` when no repair is required.
9. Approval means the current PR/head is technically ready for a human integration decision; it never authorizes merge.
10. Do not weaken or reinterpret the issue acceptance criteria merely to approve the current implementation.
11. If Human-requested rework context is present, treat its baselineHead and directives as authoritative continuation acceptance criteria. Inspect `git diff <baselineHead>...HEAD` in addition to the whole PR. Do not approve unless the delta since that rejected/human-gated generation materially satisfies every applicable human directive. A changed SHA, fresh tests, or unrelated/test-only edits are not evidence that a requested runtime/architecture change was implemented. If a directive is impossible, contradictory, or requires scope expansion, use `blocked_or_ambiguous` rather than ignoring it.
12. When one or more fresh Unity visual captures are attached, inspect the pixels themselves. Evaluate environment/world coherence, spatial readability, visual hierarchy, asset integration and obvious repetition, actor/target readability, and whether the viewed space reads as an authored game environment rather than a test arena. Make only claims supported by the attached view; mark anything outside the frame as unassessed rather than inferring it from hierarchy or transforms.
13. For each attached Unity capture, inspect the sibling `visual-diagnostics.json`, `shader-log.txt`, and `manifest.json` in the same Logs/SymphonyUnity capture directory when they exist. Treat these as capture-environment evidence.
14. Magenta/pink pixels alone do not prove that the PR has broken materials. A material/shader defect may be reported as `changes_required` only when the capture diagnostics support it—for example a renderer in the camera frustum has a missing shader, `shaderSupported=false`, or the shader log contains a matching compilation/unsupported-subshader failure. Name the diagnostic evidence in the finding.
15. If the image appears materially corrupted (for example widespread magenta) but in-frustum shaders are present/supported and the shader log does not corroborate a shader failure, treat the visual evidence as capture-tool uncertainty rather than a code/art defect. Use `blocked_or_ambiguous`, `requires_human=true`, `reason=insufficient_evidence`, no repair routing, and explain that normal-editor comparison or a trustworthy recapture is required.
16. Record the capture render method, render pipeline, and graphics device when they materially affect confidence. Do not infer that a batch/headless rendering anomaly will reproduce in the normal player/editor without corroborating evidence.

17. Always populate human_rework_assessment. Use null when Human-requested rework context is absent. When it is present, report the exact baselineHead/currentHead, whether every applicable directive is satisfied, and concrete repository paths from the baseline...HEAD delta that prove satisfaction. Do not cite unchanged files or validation artifacts as implementation evidence.

Return only the structured verdict required by the provided output schema.
"""


def fresh_visual_capture_images(workspace: Path) -> list[Path]:
    """Return captures produced after the current checked-out HEAD commit.

    Capture artifacts are ignored workspace evidence rather than Git state. Requiring
    their mtime to be at least the HEAD commit time prevents an older scene image from
    being silently attached to review of a newer PR head.
    """
    try:
        head_epoch = int(run_git(workspace, "show", "-s", "--format=%ct", "HEAD"))
    except (RuntimeError, ValueError):
        return []

    candidates: list[tuple[float, Path, bool]] = []
    unity_root = workspace / "Logs" / "SymphonyUnity"
    if not unity_root.is_dir():
        return []

    for summary_path in unity_root.glob("*/summary.json"):
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(summary, dict) or summary.get("result") != "Captured":
            continue
        image_name = str(summary.get("image") or "scene.png")
        image_path = summary_path.parent / image_name
        diagnostics_name = str(summary.get("diagnostics") or "visual-diagnostics.json")
        diagnostics_path = summary_path.parent / diagnostics_name
        try:
            modified = image_path.stat().st_mtime
        except OSError:
            continue
        if modified < head_epoch:
            continue
        if image_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        candidates.append((modified, image_path, diagnostics_path.is_file()))

    # Once a diagnostics-capable capture exists for this PR head, do not mix it with legacy
    # captures produced by the pre-fidelity harness. Mixing old known-bad pixels into a new review
    # would reintroduce exactly the false-positive path the diagnostics contract is meant to close.
    if any(has_diagnostics for _, _, has_diagnostics in candidates):
        candidates = [item for item in candidates if item[2]]

    candidates.sort(key=lambda item: item[0])
    return [path for _, path, _ in candidates[-4:]]


def run_reviewer(issue: dict[str, Any], pr: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    workspace = WORKSPACE_ROOT / f"GH-{issue['number']}"
    if not workspace.is_dir():
        raise RuntimeError(f"workspace missing: {workspace}")
    branch = run_git(workspace, "branch", "--show-current")
    if branch != pr["head"]["ref"]:
        raise RuntimeError(f"workspace branch {branch!r} does not match PR head {pr['head']['ref']!r}")
    local_head = run_git(workspace, "rev-parse", "HEAD")
    if local_head != pr["head"]["sha"]:
        raise RuntimeError(f"workspace HEAD {local_head} does not match PR head {pr['head']['sha']}")

    model, effort, route = reviewer_route(labels(issue))
    run_dir = STATE_ROOT / "review-runs" / f"GH-{issue['number']}" / f"cycle-{state['reviewCycle']}-{pr['head']['sha'][:12]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = run_dir / "prompt.txt"
    output_path = run_dir / "verdict.json"
    prompt_path.write_text(build_prompt(issue, pr, state, route), encoding="utf-8")
    visual_images = fresh_visual_capture_images(workspace)
    subprocess.run(
        [
            str(SUPERVISOR_ROOT / "scripts/review-worker.sh"),
            str(workspace),
            str(prompt_path),
            str(output_path),
            model,
            effort,
            *[str(path) for path in visual_images],
        ],
        check=True,
    )
    verdict = json.loads(output_path.read_text(encoding="utf-8"))
    verdict["reviewerRoute"] = route
    verdict["reviewedHead"] = pr["head"]["sha"]
    return verdict


def state_comment(state: dict[str, Any], verdict: dict[str, Any]) -> str:
    lines = [f"### Automated review cycle {state['reviewCycle']}: `{verdict['verdict']}`", "", verdict["summary"]]
    findings = verdict.get("findings") or []
    if findings:
        lines += ["", "Findings:"]
        for finding in findings:
            path = f" (`{finding['path']}`)" if finding.get("path") else ""
            lines.append(f"- **{finding['severity']} / {finding['category']}**{path}: {finding['description']} — {finding['suggested_action']}")
    lines += ["", f"Repair routing recommendation: `{verdict['routing_recommendation']}`.", f"Reviewed head: `{verdict['reviewedHead']}`."]
    return "\n".join(lines)


def human_review_packet(issue: dict[str, Any], pr: dict[str, Any], state: dict[str, Any]) -> str:
    files = changed_files(int(pr["number"]))
    history = state.get("history") or []
    repaired = [item for item in history[:-1] if item.get("verdict") == "changes_required"]
    lines = [
        "## Human Review packet", "",
        f"- Issue: GH-{issue['number']} — {issue.get('title','')}",
        f"- PR: #{pr['number']} — {pr.get('title','')}",
        f"- Head: `{pr['head']['sha']}`",
        f"- Automated review passes: {len(history)}",
        f"- Automatic repairs consumed: {state['repairAttempts']} / {state['maxRepairAttempts']}",
        "- Integration: **human approval required; no automated merge is permitted**", "",
        "### Files changed",
    ]
    lines.extend(f"- `{name}`" for name in files[:100])
    if not files:
        lines.append("- unavailable")
    lines += ["", "### Review history"]
    for item in history:
        lines.append(f"- Cycle {item['cycle']} — `{item['verdict']}` on `{item['head'][:12]}` via `{item['reviewerRoute']}`: {item['summary']}")
    if repaired:
        lines += ["", "### Findings repaired during the automated loop"]
        for item in repaired:
            for finding in item.get("findings") or []:
                lines.append(f"- Cycle {item['cycle']}: {finding['description']}")
    lines += ["", "### Validation / implementation notes", pr.get("body") or "PR description unavailable."]
    return "\n".join(lines)


def remove_lifecycle_except(number: int, keep: set[str]) -> None:
    for name in ("symphony:agent-review", "symphony:rework", "symphony:human-review", "symphony:human-attention"):
        if name not in keep:
            remove_label(number, name)


def set_repair_route(number: int, recommendation: str) -> None:
    for name in ("repair-route:luna", "repair-route:terra", "repair-route:sol", "repair-route:astra"):
        remove_label(number, name)
    if recommendation in {"luna", "sol"}:
        add_labels(number, f"repair-route:{recommendation}")


def history_entry(state: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    return {
        "cycle": state["reviewCycle"], "head": verdict["reviewedHead"], "verdict": verdict["verdict"],
        "summary": verdict["summary"], "findings": verdict.get("findings") or [],
        "routingRecommendation": verdict["routing_recommendation"], "reviewerRoute": verdict["reviewerRoute"],
        "reason": verdict.get("reason", "none"),
    }


def dispatch_rework(number: int, state: dict[str, Any]) -> None:
    # Keep agent-review until the replacement dispatch lease exists. If Supervisor dies before
    # that point, the next poll can resume the persisted transition. Remove agent-review last.
    add_labels(number, "symphony:rework")
    set_repair_route(number, str(state.get("routingRecommendation") or "unchanged"))
    add_labels(number, "symphony:rearm")
    add_labels(number, "symphony:ready")
    remove_lifecycle_except(number, {"symphony:rework"})


def reconcile_prior_state(issue: dict[str, Any], workspace: Path, pr: dict[str, Any], prior: dict[str, Any]) -> bool:
    number = int(issue["number"])
    current = labels(issue)
    state = prior.get("state")
    same_reviewed_head = head_already_reviewed(pr, prior)
    same_recorded_generation = head_matches_recorded_generation(pr, prior)
    if state == "human_review":
        if repair_completed_since_state(workspace, prior) and not same_recorded_generation:
            return False
        add_labels(number, "symphony:human-review")
        set_repair_route(number, "unchanged")
        remove_lifecycle_except(number, {"symphony:human-review"})
        return True
    if state == "human_attention":
        if repair_completed_since_state(workspace, prior) and not same_recorded_generation:
            return False
        add_labels(number, "symphony:human-attention")
        set_repair_route(number, "unchanged")
        remove_lifecycle_except(number, {"symphony:human-attention"})
        return True
    if state == "human_rework":
        # The rejected PR head is never reviewable again. Human rework does not consume the
        # automated repair budget; only a materially advanced PR head may proceed to review.
        if same_recorded_generation:
            add_labels(number, "symphony:rework")
            remove_label(number, "symphony:agent-review")
            return True
        if "symphony:ready" in current or "symphony:rearm" in current:
            add_labels(number, "symphony:rework")
            remove_label(number, "symphony:agent-review")
            return True
        if not repair_completed_since_state(workspace, prior):
            add_labels(number, "symphony:rework")
            remove_label(number, "symphony:agent-review")
            return True
        # A completed continuation that advanced the rejected head is eligible for independent review.
        return False
    if state == "rework":
        if "symphony:ready" in current or "symphony:rearm" in current:
            add_labels(number, "symphony:rework")
            remove_label(number, "symphony:agent-review")
            return True
        if same_reviewed_head and repair_completed_since_state(workspace, prior):
            # A preflight/host halt may still write the worker-attempt marker and queue agent
            # review even though no repair commit reached the PR. Never spend another reviewer
            # cycle on a head that the latest review already evaluated.
            add_labels(number, "symphony:rework")
            remove_label(number, "symphony:agent-review")
            return True
        if not repair_completed_since_state(workspace, prior):
            dispatch_rework(number, prior)
            return True
    return False


def process(issue: dict[str, Any]) -> None:
    number = int(issue["number"])
    workspace = WORKSPACE_ROOT / f"GH-{number}"
    if not workspace.is_dir():
        raise RuntimeError(f"cannot review GH-{number}: workspace missing")
    branch = run_git(workspace, "branch", "--show-current")
    pr = open_pr(branch)
    if not pr:
        state = {"state":"human_attention","reason":"missing_pr","reviewCycle":0,"repairAttempts":0,"maxRepairAttempts":MAX_REPAIRS,"history":[]}
        persist_state(number, state, "Automated review halted because no open PR matches the issue workspace branch.")
        add_labels(number, "symphony:human-attention")
        remove_lifecycle_except(number, {"symphony:human-attention"})
        return

    prior = latest_state(number)
    if prior and reconcile_prior_state(issue, workspace, pr, prior):
        return

    repairs = int(prior.get("repairAttempts", 0))
    prior_history = prior.get("history") if isinstance(prior.get("history"), list) else []
    state = {
        "state": "agent_review", "issue": number, "prNumber": int(pr["number"]), "prHeadSha": pr["head"]["sha"],
        "reviewCycle": len(prior_history) + 1, "repairAttempts": repairs, "maxRepairAttempts": MAX_REPAIRS,
        "history": list(prior_history),
    }
    rework_context = human_rework_context(number, prior, pr["head"]["sha"]) if prior else None
    if rework_context is not None:
        state["humanRework"] = rework_context
    else:
        inherited_rework = prior.get("humanRework")
        if isinstance(inherited_rework, dict):
            # The rejected baseline and directives are durable across reviewed continuations,
            # but currentHead describes the generation being reviewed. Never inherit a stale
            # currentHead after the PR advances through a later human-authorized repair.
            state["humanRework"] = dict(inherited_rework)
            state["humanRework"]["currentHead"] = pr["head"]["sha"]
    verdict = run_reviewer(issue, pr, state)
    verdict = enforce_human_rework_acceptance(workspace, state, verdict)
    state["lastVerdict"] = verdict["verdict"]
    state["lastSummary"] = verdict["summary"]
    state["routingRecommendation"] = verdict["routing_recommendation"]
    state["reason"] = verdict.get("reason", "none")
    state["history"].append(history_entry(state, verdict))

    review_text = state_comment(state, verdict)
    post_comment(int(pr["number"]), review_text)

    if verdict["verdict"] == "approved" and not verdict.get("requires_human"):
        state["state"] = "human_review"
        persist_state(number, state, review_text + "\n\nAutomated review passed. Human approval is now required for merge.")
        add_labels(number, "symphony:human-review")
        set_repair_route(number, "unchanged")
        remove_lifecycle_except(number, {"symphony:human-review"})
        post_comment(int(pr["number"]), human_review_packet(issue, pr, state))
        return

    if verdict["verdict"] == "blocked_or_ambiguous" or verdict.get("requires_human"):
        state["state"] = "human_attention"
        persist_state(number, state, review_text + "\n\nAutomation stopped for human attention; no repair was dispatched.")
        add_labels(number, "symphony:human-attention")
        set_repair_route(number, "unchanged")
        remove_lifecycle_except(number, {"symphony:human-attention"})
        return

    if repairs >= MAX_REPAIRS:
        state["state"] = "human_attention"
        state["reason"] = "review_loop_exhausted"
        persist_state(number, state, review_text + f"\n\nAutomatic repair budget exhausted ({MAX_REPAIRS}); human attention is required.")
        add_labels(number, "symphony:human-attention")
        set_repair_route(number, "unchanged")
        remove_lifecycle_except(number, {"symphony:human-attention"})
        return

    state["state"] = "rework"
    state["repairAttempts"] = repairs + 1
    persist_state(number, state, review_text + f"\n\nAutomatic repair {state['repairAttempts']} of {MAX_REPAIRS} approved for dispatch against the existing PR/branch.")
    dispatch_rework(number, state)


def retrospective_visual_review(issue_number: int, pr_number: int) -> None:
    issue = api("GET", f"/issues/{issue_number}")
    pr = api("GET", f"/pulls/{pr_number}")
    if not isinstance(issue, dict) or int(issue.get("number", 0)) != issue_number:
        raise RuntimeError(f"issue GH-{issue_number} could not be loaded")
    if not isinstance(pr, dict) or int(pr.get("number", 0)) != pr_number:
        raise RuntimeError(f"PR #{pr_number} could not be loaded")
    if pr.get("state") != "open" or pr.get("draft"):
        raise RuntimeError(f"PR #{pr_number} must be an open non-draft PR")
    issue_labels = labels(issue)
    if not ({"symphony:human-review", "symphony:human-attention"} & issue_labels):
        raise RuntimeError(
            f"GH-{issue_number} is not at a retrospective visual-review gate "
            "(expected symphony:human-review or symphony:human-attention)"
        )

    prior = latest_state(issue_number)
    if not prior or prior.get("state") not in {"human_review", "human_attention"}:
        raise RuntimeError(
            f"GH-{issue_number} has no durable human-review/human-attention state"
        )
    if int(prior.get("prNumber", 0)) != pr_number:
        raise RuntimeError(
            f"GH-{issue_number} durable review state points to PR #{prior.get('prNumber')}, not #{pr_number}"
        )
    workspace = WORKSPACE_ROOT / f"GH-{issue_number}"
    if not workspace.is_dir():
        raise RuntimeError(f"cannot visually review GH-{issue_number}: workspace missing")
    branch = run_git(workspace, "branch", "--show-current")
    if branch != pr["head"]["ref"]:
        raise RuntimeError(f"workspace branch {branch!r} does not match PR head {pr['head']['ref']!r}")
    local_head = run_git(workspace, "rev-parse", "HEAD")
    if local_head != pr["head"]["sha"]:
        raise RuntimeError(f"workspace HEAD {local_head} does not match PR head {pr['head']['sha']}")

    prior_head = str(prior.get("prHeadSha") or "")
    current_head = str(pr["head"]["sha"])
    if prior_head != current_head:
        try:
            run_git(workspace, "merge-base", "--is-ancestor", prior_head, current_head)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "PR head moved non-fast-forward after the durable human-review state was recorded"
            ) from exc

    visual_images = fresh_visual_capture_images(workspace)
    if not visual_images:
        raise RuntimeError(
            "no fresh Unity visual capture exists for the current PR head; run the bounded capture command first"
        )

    prior_history = prior.get("history") if isinstance(prior.get("history"), list) else []
    state = dict(prior)
    state["reviewCycle"] = len(prior_history) + 1
    state["history"] = list(prior_history)
    state["prHeadSha"] = pr["head"]["sha"]
    state["prNumber"] = pr_number

    verdict = run_reviewer(issue, pr, state)
    state["lastVerdict"] = verdict["verdict"]
    state["lastSummary"] = verdict["summary"]
    state["routingRecommendation"] = verdict["routing_recommendation"]
    state["reason"] = verdict.get("reason", "none")
    state["history"].append(history_entry(state, verdict))

    review_text = state_comment(state, verdict)
    prefixed = "### Retrospective Unity visual review\n\n" + review_text
    post_comment(pr_number, prefixed)

    if verdict["verdict"] == "approved" and not verdict.get("requires_human"):
        state["state"] = "human_review"
        persist_state(
            issue_number,
            state,
            prefixed
            + "\n\nVisual review passed. The issue remains at the human integration gate; no merge was authorized.",
        )
        add_labels(issue_number, "symphony:human-review")
        set_repair_route(issue_number, "unchanged")
        remove_lifecycle_except(issue_number, {"symphony:human-review"})
        post_comment(pr_number, human_review_packet(issue, pr, state))
        return

    # This path is intentionally observational. A retrospective visual finding never
    # auto-rearms implementation or spends a repair attempt.
    state["state"] = "human_attention"
    state["reason"] = verdict.get("reason", "visual_review_finding") or "visual_review_finding"
    persist_state(
        issue_number,
        state,
        prefixed
        + "\n\nVisual review found a concern or ambiguity. Automation stopped for human attention; no repair was dispatched.",
    )
    add_labels(issue_number, "symphony:human-attention")
    set_repair_route(issue_number, "unchanged")
    remove_lifecycle_except(issue_number, {"symphony:human-attention"})


def pending_visual_review_comment(issue_number: int) -> int | None:
    comments = api("GET", f"/issues/{issue_number}/comments?per_page=100") or []
    completed: set[int] = set()
    requests: list[int] = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        body = str(comment.get("body") or "")
        start = body.find(VISUAL_REVIEW_COMPLETE_PREFIX)
        if start >= 0:
            value_start = start + len(VISUAL_REVIEW_COMPLETE_PREFIX)
            value_end = body.find(" -->", value_start)
            if value_end > value_start:
                try:
                    completed.add(int(body[value_start:value_end]))
                except ValueError:
                    pass

        if VISUAL_REVIEW_REQUEST_MARKER in body:
            raw_id = comment.get("id")
            try:
                requests.append(int(raw_id))
            except (TypeError, ValueError):
                continue
    for comment_id in reversed(requests):
        if comment_id not in completed:
            return comment_id
    return None


def visual_review_profile(issue: dict[str, Any]) -> dict[str, Any] | None:
    body = str(issue.get("body") or "")
    start = body.find(VISUAL_REVIEW_REQUIREMENTS_MARKER)
    if start < 0:
        return None
    payload_start = body.find("{", start + len(VISUAL_REVIEW_REQUIREMENTS_MARKER))
    end = body.find("-->", payload_start)
    if payload_start < 0 or end < 0:
        raise RuntimeError("visual-review requirements block is malformed")
    try:
        profile = json.loads(body[payload_start:end].strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"visual-review requirements JSON is invalid: {exc}") from exc
    if not isinstance(profile, dict):
        raise RuntimeError("visual-review requirements must be a JSON object")
    views = profile.get("views")
    if not isinstance(views, list) or not (1 <= len(views) <= 4):
        raise RuntimeError("visual-review requirements must define 1 to 4 views")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(views):
        if not isinstance(raw, dict):
            raise RuntimeError(f"visual-review view {index + 1} must be an object")
        name = str(raw.get("name") or f"view-{index + 1}").strip()
        camera = str(raw.get("camera") or "").strip()
        look_at = str(raw.get("lookAt") or "").strip()
        position = raw.get("position")
        rotation = raw.get("rotation")
        fov = float(raw.get("fov", 60.0))
        if not (1.0 < fov < 179.0):
            raise RuntimeError(f"visual-review view {name!r} has invalid fov")
        if position is not None:
            if not isinstance(position, list) or len(position) != 3 or not all(isinstance(v, (int, float)) for v in position):
                raise RuntimeError(f"visual-review view {name!r} position must be [x,y,z]")
        if rotation is not None:
            if not isinstance(rotation, list) or len(rotation) != 3 or not all(isinstance(v, (int, float)) for v in rotation):
                raise RuntimeError(f"visual-review view {name!r} rotation must be [x,y,z]")
        if rotation is not None and look_at:
            raise RuntimeError(f"visual-review view {name!r} may specify rotation or lookAt, not both")
        normalized.append({
            "name": name,
            "camera": camera,
            "position": position,
            "rotation": rotation,
            "lookAt": look_at,
            "fov": fov,
        })
    return {"views": normalized}


def visual_review_scene(issue: dict[str, Any]) -> str:
    body = str(issue.get("body") or "")
    marker = "<!-- symphony-scene-authoring-requirements"
    start = body.find(marker)
    if start < 0:
        raise RuntimeError("visual-review request requires scene-authoring requirements with an exact scene")
    payload_start = body.find("{", start + len(marker))
    end = body.find("-->", payload_start)
    if payload_start < 0 or end < 0:
        raise RuntimeError("scene-authoring requirements block is malformed")
    try:
        requirements = json.loads(body[payload_start:end].strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"scene-authoring requirements JSON is invalid: {exc}") from exc
    scene = str(requirements.get("scene") or "")
    if not scene.startswith("Assets/") or not scene.endswith(".unity") or ".." in scene:
        raise RuntimeError("visual-review request requires a normalized exact Assets/*.unity scene")
    return scene


def complete_visual_review_request(issue_number: int, comment_request_id: int | None, message: str) -> None:
    remove_label(issue_number, "symphony:visual-review")
    if comment_request_id is not None:
        post_comment(
            issue_number,
            message + "\n\n" + f"{VISUAL_REVIEW_COMPLETE_PREFIX}{comment_request_id} -->",
        )


def process_visual_review_request(issue: dict[str, Any]) -> None:
    number = int(issue["number"])
    comment_request_id = pending_visual_review_comment(number)
    record_visual_review_event(number, "detected", summary="One-shot retrospective visual-review request detected.")
    issue_labels = labels(issue)
    if not ({"symphony:human-review", "symphony:human-attention"} & issue_labels):
        post_comment(
            number,
            "Retrospective visual review request was rejected because the issue is not at "
            "`symphony:human-review` or `symphony:human-attention`. "
            "No implementation or review worker was started.",
        )
        complete_visual_review_request(
            number,
            comment_request_id,
            "Retrospective Unity visual review request was consumed without running.",
        )
        return

    prior = latest_state(number)
    if not prior or prior.get("state") not in {"human_review", "human_attention"}:
        post_comment(
            number,
            "Retrospective visual review request was rejected because durable review state is "
            "not at `human_review` or `human_attention`. "
            "No implementation or review worker was started.",
        )
        complete_visual_review_request(
            number,
            comment_request_id,
            "Retrospective Unity visual review request was consumed without running.",
        )
        return

    pr_number = int(prior.get("prNumber", 0))
    if pr_number <= 0:
        raise RuntimeError("durable human-review state does not identify a PR")
    scene = visual_review_scene(issue)
    profile = visual_review_profile(issue)
    start_visual_review_status(number, pr_number, scene, profile, comment_request_id)
    update_visual_review_status(
        number,
        phase="capture_starting",
        summary=(
            f"Acquiring Unity and capturing {scene}"
            + (f" across {len(profile['views'])} configured views." if profile else ".")
        ),
    )
    record_visual_review_event(
        number,
        "capture_starting",
        pr_number=pr_number,
        summary=(
            f"Acquiring Unity and capturing {scene} for retrospective review"
            + (f" across {len(profile['views'])} configured views." if profile else ".")
        ),
    )

    command = [
        "bash",
        str(SUPERVISOR_ROOT / "scripts/visual-review-pr.sh"),
        "--issue", str(number),
        "--pr", str(pr_number),
        "--scene", scene,
    ]
    if profile is not None:
        profile_dir = STATE_ROOT / "visual-review-profiles"
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_path = profile_dir / f"GH-{number}.json"
        profile_path.write_text(json.dumps(profile, separators=(",", ":")), encoding="utf-8")
        command.extend(["--profile", str(profile_path)])

    subprocess.run(command, check=True)
    record_visual_review_event(
        number,
        "completed",
        pr_number=pr_number,
        summary="Retrospective Unity visual review completed.",
    )
    archive_visual_review_status(
        number,
        "completed",
        "Retrospective Unity visual review completed.",
    )
    complete_visual_review_request(
        number,
        comment_request_id,
        "Retrospective Unity visual review request completed.",
    )


def lifecycle_issues(label: str) -> list[dict[str, Any]]:
    q = parse.urlencode({"state":"open", "labels":label, "per_page":50})
    values = api("GET", f"/issues?{q}") or []
    return [item for item in values if "pull_request" not in item]


def visual_review_requests() -> list[dict[str, Any]]:
    requested: dict[int, dict[str, Any]] = {
        int(issue["number"]): issue for issue in lifecycle_issues("symphony:visual-review")
    }
    for label in ("symphony:human-review", "symphony:human-attention"):
        for issue in lifecycle_issues(label):
            number = int(issue["number"])
            if number not in requested and pending_visual_review_comment(number) is not None:
                requested[number] = issue
    return list(requested.values())


def reviewable_issues() -> list[dict[str, Any]]:
    # Agent-review stays present until a replacement rework dispatch is fully established, so it
    # is sufficient for restart recovery. Do not poll rework labels: there is a legitimate short
    # interval after implementation handoff removes ready and before after_run queues re-review.
    return lifecycle_issues("symphony:agent-review")


def once() -> None:
    recover_stale_visual_review_statuses()

    for issue in visual_review_requests():
        try:
            process_visual_review_request(issue)
        except Exception as exc:
            number = int(issue.get("number", 0) or 0)
            message = f"{type(exc).__name__}: {exc}"
            print(
                f"review-orchestrator: GH-{number} visual review failed: {message}",
                file=sys.stderr,
                flush=True,
            )
            if number > 0:
                archive_visual_review_status(
                    number,
                    "failed",
                    f"Retrospective visual review failed: {message[:500]}",
                )
                record_visual_review_event(
                    number,
                    "failed",
                    summary=f"Retrospective visual review failed: {message[:500]}",
                )
                request_id = pending_visual_review_comment(number)
                complete_visual_review_request(
                    number,
                    request_id,
                    "Retrospective Unity visual review failed before producing a verdict. "
                    f"Failure: `{message[:700]}`. "
                    "No implementation worker, repair, or merge was started. "
                    "After resolving the reported condition, submit a new visual-review request.",
                )

    for issue in reviewable_issues():
        try:
            process(issue)
        except Exception as exc:
            print(f"review-orchestrator: GH-{issue.get('number')} failed: {exc}", file=sys.stderr, flush=True)


def main() -> int:
    if not TOKEN:
        print("review-orchestrator: SYMPHONY_GITHUB_TOKEN is required", file=sys.stderr)
        return 64
    if "--visual-review" in sys.argv:
        index = sys.argv.index("--visual-review")
        try:
            issue_number = int(sys.argv[index + 1])
            pr_number = int(sys.argv[index + 2])
        except (IndexError, ValueError):
            print("usage: review-orchestrator.py --visual-review <issue> <pr>", file=sys.stderr)
            return 64
        retrospective_visual_review(issue_number, pr_number)
        return 0
    if "--once" in sys.argv:
        once()
        return 0
    print(f"RPG Kingdom review orchestrator: polling every {POLL_SECONDS}s", flush=True)
    while True:
        once()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
