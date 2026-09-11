#!/usr/bin/env python3
"""GitHub-backed automated review/rework state machine for RPG Kingdom Supervisor."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
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


def latest_state(number: int) -> dict[str, Any]:
    comments = api("GET", f"/issues/{number}/comments?per_page=100") or []
    for comment in reversed(comments):
        body = str(comment.get("body") or "")
        start = body.find(MARKER)
        if start < 0:
            continue
        end = body.find("\n-->", start)
        if end < 0:
            continue
        raw = body[start + len(MARKER):end]
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    return {}


def persist_state(issue_number: int, state: dict[str, Any], human_text: str) -> None:
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":"))
    post_comment(issue_number, f"{human_text}\n\n{MARKER}{encoded}\n-->")


def run_git(workspace: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(workspace), *args], check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return completed.stdout.strip()


def open_pr(branch: str) -> dict[str, Any] | None:
    owner = REPO.split("/", 1)[0]
    query = parse.urlencode({"state": "open", "head": f"{owner}:{branch}", "base": "main", "per_page": 10})
    values = api("GET", f"/pulls?{query}") or []
    return values[0] if values else None


def reviewer_route(issue_labels: set[str]) -> tuple[str, str, str]:
    override = os.environ.get("RPGK_REVIEW_MODEL")
    if override:
        return override, os.environ.get("RPGK_REVIEW_EFFORT", "medium"), "override"
    if "risk:architecture" in issue_labels or "risk:end-to-end" in issue_labels:
        return "gpt-5.6-sol", "high", "sol"
    return "gpt-5.6-terra", "medium", "terra"


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

Review requirements:
1. Read repository-root AGENTS.md and only the architecture/system docs it requires for the changed scope.
2. Inspect the actual current diff with `git diff origin/main...HEAD` and changed-file list. The repository state, not prior worker prose, is authoritative.
3. Inspect relevant tests and the latest Supervisor Unity artifacts under Logs/SymphonyUnity when validation is material.
4. Check correctness, architecture/system boundaries, regression risk, persistence/save contracts, tests, docs, and whether validation actually proves the changed behavior.
5. Do not manufacture blockers. Minor non-blocking suggestions may be findings, but `changes_required` should mean the PR should not enter human integration review yet.
6. If the correct fix requires material work outside the approved issue scope, choose `blocked_or_ambiguous`, set requires_human=true, and reason=scope_change_required rather than silently expanding scope.
7. If product/design intent is genuinely ambiguous, choose `blocked_or_ambiguous` and require human attention.
8. `routing_recommendation` is advisory for a repair task. Recommend the cheapest route that is likely to resolve the actual findings; use `unchanged` when no repair is required.
9. Approval means the current PR/head is technically ready for a human integration decision; it never authorizes merge.

Return only the structured verdict required by the provided output schema.
"""


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
    subprocess.run(
        [str(SUPERVISOR_ROOT / "scripts/review-worker.sh"), str(workspace), str(prompt_path), str(output_path), model, effort],
        check=True,
    )
    verdict = json.loads(output_path.read_text(encoding="utf-8"))
    verdict["reviewerRoute"] = route
    verdict["reviewedHead"] = pr["head"]["sha"]
    return verdict


def state_comment(state: dict[str, Any], verdict: dict[str, Any]) -> str:
    lines = [
        f"### Automated review cycle {state['reviewCycle']}: `{verdict['verdict']}`",
        "",
        verdict["summary"],
    ]
    findings = verdict.get("findings") or []
    if findings:
        lines += ["", "Findings:"]
        for finding in findings:
            path = f" (`{finding['path']}`)" if finding.get("path") else ""
            lines.append(f"- **{finding['severity']} / {finding['category']}**{path}: {finding['description']} — {finding['suggested_action']}")
    lines += [
        "",
        f"Repair routing recommendation: `{verdict['routing_recommendation']}`.",
        f"Reviewed head: `{verdict['reviewedHead']}`.",
    ]
    return "\n".join(lines)


def clear_lifecycle_labels(number: int) -> None:
    for name in ("symphony:agent-review", "symphony:rework", "symphony:human-review", "symphony:human-attention"):
        remove_label(number, name)


def set_repair_route(number: int, recommendation: str) -> None:
    for name in ("repair-route:luna", "repair-route:terra", "repair-route:sol", "repair-route:astra"):
        remove_label(number, name)
    if recommendation in {"luna", "terra", "sol", "astra"}:
        add_labels(number, f"repair-route:{recommendation}")


def process(issue: dict[str, Any]) -> None:
    number = int(issue["number"])
    workspace = WORKSPACE_ROOT / f"GH-{number}"
    if not workspace.is_dir():
        raise RuntimeError(f"cannot review GH-{number}: workspace missing")
    branch = run_git(workspace, "branch", "--show-current")
    pr = open_pr(branch)
    if not pr:
        clear_lifecycle_labels(number)
        add_labels(number, "symphony:human-attention")
        persist_state(number, {"state":"human_attention","reason":"missing_pr","reviewCycle":0,"repairAttempts":0,"maxRepairAttempts":MAX_REPAIRS}, "Automated review halted because no open PR matches the issue workspace branch.")
        return

    prior = latest_state(number)
    repairs = int(prior.get("repairAttempts", 0))
    state = {
        "state": "agent_review",
        "issue": number,
        "prNumber": int(pr["number"]),
        "prHeadSha": pr["head"]["sha"],
        "reviewCycle": repairs + 1,
        "repairAttempts": repairs,
        "maxRepairAttempts": MAX_REPAIRS,
    }
    verdict = run_reviewer(issue, pr, state)
    state["lastVerdict"] = verdict["verdict"]
    state["lastSummary"] = verdict["summary"]
    state["routingRecommendation"] = verdict["routing_recommendation"]
    state["reason"] = verdict.get("reason", "none")

    # Publish the independent review to the PR conversation and durable issue state.
    review_text = state_comment(state, verdict)
    post_comment(int(pr["number"]), review_text)

    if verdict["verdict"] == "approved" and not verdict.get("requires_human"):
        state["state"] = "human_review"
        clear_lifecycle_labels(number)
        set_repair_route(number, "unchanged")
        add_labels(number, "symphony:human-review")
        persist_state(number, state, review_text + "\n\nAutomated review passed. Human approval is now required for merge.")
        return

    if verdict["verdict"] == "blocked_or_ambiguous" or verdict.get("requires_human"):
        state["state"] = "human_attention"
        clear_lifecycle_labels(number)
        add_labels(number, "symphony:human-attention")
        persist_state(number, state, review_text + "\n\nAutomation stopped for human attention; no repair was dispatched.")
        return

    if repairs >= MAX_REPAIRS:
        state["state"] = "human_attention"
        state["reason"] = "review_loop_exhausted"
        clear_lifecycle_labels(number)
        add_labels(number, "symphony:human-attention")
        persist_state(number, state, review_text + f"\n\nAutomatic repair budget exhausted ({MAX_REPAIRS}); human attention is required.")
        return

    state["state"] = "rework"
    state["repairAttempts"] = repairs + 1
    clear_lifecycle_labels(number)
    add_labels(number, "symphony:rework")
    set_repair_route(number, verdict["routing_recommendation"])
    # Reviewed continuation contract: one-shot rearm first, dispatch lease last.
    add_labels(number, "symphony:rearm")
    add_labels(number, "symphony:ready")
    persist_state(number, state, review_text + f"\n\nAutomatic repair {state['repairAttempts']} of {MAX_REPAIRS} dispatched against the existing PR/branch.")


def reviewable_issues() -> list[dict[str, Any]]:
    q = parse.urlencode({"state":"open", "labels":"symphony:agent-review", "per_page":50})
    values = api("GET", f"/issues?{q}") or []
    return [item for item in values if "pull_request" not in item]


def once() -> None:
    for issue in reviewable_issues():
        try:
            process(issue)
        except Exception as exc:
            print(f"review-orchestrator: GH-{issue.get('number')} failed: {exc}", file=sys.stderr, flush=True)


def main() -> int:
    if not TOKEN:
        print("review-orchestrator: SYMPHONY_GITHUB_TOKEN is required", file=sys.stderr)
        return 64
    if "--once" in sys.argv:
        once()
        return 0
    print(f"RPG Kingdom review orchestrator: polling every {POLL_SECONDS}s", flush=True)
    while True:
        once()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
