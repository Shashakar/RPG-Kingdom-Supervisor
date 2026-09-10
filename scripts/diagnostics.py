#!/usr/bin/env python3
"""Local diagnostics for RPG Kingdom Symphony issues.

This intentionally reads existing local/GitHub state. It does not rearm issues,
start workers, mutate repositories, or run Unity. The localhost server binds to
127.0.0.1 only and uses the same collector as the CLI output.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

DEFAULT_REPO = "Shashakar/RPG-Kingdom"
ERROR_TERMS = (
    "Read-only file system",
    "FETCH_HEAD",
    "index.lock",
    "UtilBindVsockAnyPort",
    "socket failed",
    "permission denied",
    "Permission denied",
)


def run(command: list[str], cwd: Path | None = None, timeout: int = 15) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "code": None, "stdout": "", "stderr": str(exc)}


def issue_from_github(number: int, repo: str) -> dict[str, Any]:
    result = run(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "number,title,state,url,updatedAt,labels,comments",
        ]
    )
    if not result["ok"]:
        return {"available": False, "error": result["stderr"] or result["stdout"]}
    try:
        raw = json.loads(result["stdout"])
    except json.JSONDecodeError as exc:
        return {"available": False, "error": f"invalid gh JSON: {exc}"}
    comments = raw.get("comments") or []
    latest_comment = comments[-1].get("body", "") if comments else ""
    return {
        "available": True,
        "number": raw.get("number"),
        "title": raw.get("title"),
        "state": raw.get("state"),
        "url": raw.get("url"),
        "updated_at": raw.get("updatedAt"),
        "labels": [item.get("name") for item in raw.get("labels", []) if item.get("name")],
        "latest_comment": latest_comment,
        "comment_count": len(comments),
    }


def workspace_state(identifier: str) -> dict[str, Any]:
    root = Path(
        os.path.expanduser(
            os.environ.get(
                "RPGK_WORKSPACE_ROOT", "~/code/rpg-kingdom-symphony-workspaces"
            )
        )
    )
    workspace = root / identifier
    result: dict[str, Any] = {
        "path": str(workspace),
        "exists": workspace.is_dir(),
        "attempt_complete": (workspace / ".symphony-attempt-complete").exists(),
    }
    if not workspace.is_dir():
        return result

    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=workspace)
    head = run(["git", "rev-parse", "HEAD"], cwd=workspace)
    status = run(["git", "status", "--short", "--branch"], cwd=workspace)
    result.update(
        {
            "branch": branch["stdout"] if branch["ok"] else None,
            "head": head["stdout"] if head["ok"] else None,
            "git_status": status["stdout"] if status["ok"] else None,
            "git_error": None if status["ok"] else status["stderr"],
        }
    )

    summaries = sorted(
        workspace.glob("Logs/SymphonyUnity/*/summary.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if summaries:
        latest = summaries[0]
        try:
            result["latest_unity"] = json.loads(latest.read_text(encoding="utf-8-sig"))
            result["latest_unity"]["summary_path"] = str(latest)
        except (OSError, json.JSONDecodeError) as exc:
            result["latest_unity"] = {"summary_path": str(latest), "error": str(exc)}
    else:
        result["latest_unity"] = None
    return result


def unity_lock_state() -> dict[str, Any]:
    state_root = Path(
        os.path.expanduser(
            os.environ.get(
                "RPGK_SUPERVISOR_STATE_ROOT", "~/.local/state/rpg-kingdom-supervisor"
            )
        )
    )
    lock = state_root / "locks" / "unity-editor.lock"
    result: dict[str, Any] = {"path": str(lock), "held": lock.is_dir()}
    if not lock.is_dir():
        return result
    for name in ("owner", "workspace", "acquired-at"):
        path = lock / name
        result[name.replace("-", "_")] = (
            path.read_text(errors="replace").strip() if path.exists() else None
        )
    return result


def read_text_tail(path: Path, max_bytes: int = 2_000_000) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                handle.readline()
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def symphony_state(identifier: str) -> dict[str, Any]:
    root = Path(
        os.path.expanduser(
            os.environ.get("RPGK_SYMPHONY_ROOT", "~/src/openai-symphony/elixir")
        )
    )
    log_dir = root / "log"
    files = [
        p
        for p in log_dir.glob("symphony.log*")
        if p.is_file() and not p.name.endswith((".idx", ".siz"))
    ]
    files.sort(key=lambda path: path.stat().st_mtime)
    matches: list[str] = []
    for path in files:
        for line in read_text_tail(path).splitlines():
            if f"issue_identifier={identifier}" in line or f" {identifier} " in line:
                matches.append(line)
    matches = matches[-120:]

    session_id = None
    thread_id = None
    turn = None
    for line in reversed(matches):
        if session_id is None:
            found = re.search(r"session_id=([^\s]+)", line)
            if found:
                session_id = found.group(1)
                thread_id = session_id[:36]
        if turn is None:
            found = re.search(r"turn=(\d+/\d+)", line)
            if found:
                turn = found.group(1)
        if session_id and turn:
            break

    return {
        "root": str(root),
        "lines": matches[-30:],
        "session_id": session_id,
        "thread_id": thread_id,
        "turn": turn,
    }


def walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_strings(child)


def rollout_state(thread_id: str | None) -> dict[str, Any]:
    if not thread_id:
        return {"available": False, "reason": "no Symphony thread id found"}
    sessions = Path(os.path.expanduser("~/.codex/sessions"))
    candidates = list(sessions.glob(f"**/rollout-*{thread_id}*.jsonl"))
    if not candidates:
        return {"available": False, "reason": f"no rollout found for {thread_id}"}
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    path = candidates[0]

    latest_tokens = None
    last_context = None
    failures: list[str] = []
    model = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                payload = record.get("payload")
                if isinstance(payload, dict):
                    ptype = payload.get("type")
                    if ptype == "token_count":
                        latest_tokens = payload.get("info")
                    if ptype == "turn_context":
                        last_context = payload
                    if ptype in ("session_meta", "thread_started"):
                        model = payload.get("model") or model
                for text in walk_strings(record):
                    if any(term in text for term in ERROR_TERMS):
                        failures.append(text)
    except OSError as exc:
        return {"available": False, "reason": str(exc), "path": str(path)}

    context_summary: dict[str, Any] = {}
    if isinstance(last_context, dict):
        flattened = "\n".join(walk_strings(last_context))
        context_summary = {
            "mentions_git": ".git" in flattened,
            "mentions_workspace_write": "workspace-write" in flattened
            or "workspaceWrite" in flattened,
            "raw": last_context,
        }

    return {
        "available": True,
        "path": str(path),
        "model": model,
        "tokens": latest_tokens,
        "turn_context": context_summary,
        "failures": failures[-10:],
    }


def collect(number: int) -> dict[str, Any]:
    identifier = f"GH-{number}"
    repo = os.environ.get("RPGK_REPO", DEFAULT_REPO)
    symphony = symphony_state(identifier)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "identifier": identifier,
        "github": issue_from_github(number, repo),
        "workspace": workspace_state(identifier),
        "unity_lock": unity_lock_state(),
        "symphony": symphony,
        "codex": rollout_state(symphony.get("thread_id")),
    }


def status(value: bool | None) -> str:
    if value is True:
        return "PASS"
    if value is False:
        return "FAIL"
    return "UNKNOWN"


def render_text(data: dict[str, Any]) -> str:
    gh = data["github"]
    ws = data["workspace"]
    sx = data["symphony"]
    cx = data["codex"]
    unity = ws.get("latest_unity")
    lines = [f"{data['identifier']} diagnostics", ""]
    if gh.get("available"):
        lines += [
            "GitHub",
            f"  State:       {gh.get('state')}",
            f"  Labels:      {', '.join(gh.get('labels', [])) or '-'}",
            f"  Comments:    {gh.get('comment_count')}",
        ]
    else:
        lines += ["GitHub", f"  Error:       {gh.get('error')}"]

    lines += [
        "",
        "Workspace",
        f"  Exists:      {ws.get('exists')}",
        f"  Branch:      {ws.get('branch') or '-'}",
        f"  Attempt used:{' yes' if ws.get('attempt_complete') else ' no'}",
    ]
    if ws.get("git_status"):
        for line in ws["git_status"].splitlines():
            lines.append(f"  Git:         {line}")

    lines += [
        "",
        "Symphony",
        f"  Session:     {sx.get('session_id') or '-'}",
        f"  Turn:        {sx.get('turn') or '-'}",
    ]

    lines += ["", "Codex"]
    if cx.get("available"):
        lines.append(f"  Rollout:     {cx.get('path')}")
        tokens = cx.get("tokens") or {}
        usage = tokens.get("total_token_usage") if isinstance(tokens, dict) else None
        if isinstance(usage, dict):
            lines.append(f"  Tokens:      {usage.get('total_tokens', '-')}")
        context = cx.get("turn_context") or {}
        lines.append(
            f"  Legacy WS:   {status(context.get('mentions_workspace_write') is False)}"
        )
        lines.append(f"  Failures:    {len(cx.get('failures', []))}")
        for failure in cx.get("failures", [])[-4:]:
            lines.append(f"    - {failure[:240]}")
    else:
        lines.append(f"  Status:      unavailable ({cx.get('reason')})")

    lines += ["", "Unity"]
    lock = data["unity_lock"]
    lines.append(f"  Lock held:   {lock.get('held')}")
    if lock.get("held"):
        lines.append(f"  Owner:       {lock.get('owner') or '-'}")
    if isinstance(unity, dict):
        lines.append(f"  Last result: {unity.get('result', '-')}")
        lines.append(f"  Platform:    {unity.get('testPlatform', '-')}")
        lines.append(f"  Run:         {unity.get('runId', '-')}")
    else:
        lines.append("  Last result: none")

    if gh.get("latest_comment"):
        lines += ["", "Latest issue comment", f"  {gh['latest_comment'][:1000]}"]
    return "\n".join(lines)


PAGE = """<!doctype html>
<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>RPG Kingdom Supervisor Diagnostics</title>
<style>
body{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;background:#111;color:#ddd;margin:0;padding:20px}h1{font:600 22px system-ui;margin:0 0 16px}.bar{display:flex;gap:8px;margin-bottom:16px}input,button{background:#1d1d1d;color:#eee;border:1px solid #444;padding:8px 10px;border-radius:6px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:12px}.card{background:#191919;border:1px solid #333;border-radius:8px;padding:12px;min-height:120px}.card h2{font:600 15px system-ui;margin:0 0 10px;color:#fff}.row{display:grid;grid-template-columns:120px 1fr;gap:8px;margin:5px 0}.muted{color:#888}.bad{color:#ff8a80}.good{color:#9ccc65}pre{white-space:pre-wrap;word-break:break-word;font-size:12px}.wide{grid-column:1/-1}</style>
</head><body><h1>RPG Kingdom Supervisor Diagnostics</h1>
<div class='bar'>GH-<input id='issue' value='97' size='5'><button onclick='load()'>Refresh</button><span id='stamp' class='muted'></span></div>
<div id='grid' class='grid'></div>
<script>
function esc(v){return String(v??'-').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function rows(obj, keys){return keys.map(([k,l])=>`<div class='row'><span class='muted'>${esc(l)}</span><span>${esc(obj?.[k])}</span></div>`).join('');}
async function load(){const n=document.getElementById('issue').value;try{const d=await fetch('/api/issue/'+encodeURIComponent(n),{cache:'no-store'}).then(r=>r.json());document.getElementById('stamp').textContent='updated '+new Date().toLocaleTimeString();const g=d.github||{},w=d.workspace||{},s=d.symphony||{},c=d.codex||{},u=d.unity_lock||{},last=w.latest_unity||{};document.getElementById('grid').innerHTML=`
<div class='card'><h2>GitHub</h2>${rows(g,[['state','state'],['labels','labels'],['comment_count','comments'],['updated_at','updated']])}</div>
<div class='card'><h2>Workspace</h2>${rows(w,[['branch','branch'],['head','head'],['attempt_complete','attempt used']])}<pre>${esc(w.git_status||'clean / unavailable')}</pre></div>
<div class='card'><h2>Symphony</h2>${rows(s,[['session_id','session'],['turn','turn'],['thread_id','thread']])}</div>
<div class='card'><h2>Codex</h2>${rows(c,[['model','model'],['path','rollout']])}<pre class='bad'>${esc((c.failures||[]).slice(-5).join('\n\n')||'no matched failures')}</pre></div>
<div class='card'><h2>Unity</h2>${rows(u,[['held','lock held'],['owner','owner']])}${rows(last,[['result','last result'],['testPlatform','platform'],['runId','run']])}</div>
<div class='card wide'><h2>Latest issue comment</h2><pre>${esc(g.latest_comment||'-')}</pre></div>
<div class='card wide'><h2>Recent Symphony lines</h2><pre>${esc((s.lines||[]).join('\n'))}</pre></div>`;}catch(e){document.getElementById('grid').innerHTML=`<div class='card bad'>${esc(e)}</div>`;}}
load();setInterval(load,5000);
</script></body></html>"""


def serve(port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                body = PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            match = re.fullmatch(r"/api/issue/(\d+)", parsed.path)
            if match:
                body = json.dumps(collect(int(match.group(1)))).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"RPG Kingdom diagnostics: http://127.0.0.1:{port}")
    print("Ctrl+C to stop. The server is read-only and bound to localhost.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    issue_parser = sub.add_parser("issue")
    issue_parser.add_argument("number", type=int)
    issue_parser.add_argument("--json", action="store_true")
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.command == "issue":
        data = collect(args.number)
        print(json.dumps(data, indent=2) if args.json else render_text(data))
        return 0
    if args.command == "serve":
        serve(args.port)
        return 0
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
