#!/usr/bin/env python3
"""Read-only localhost dashboard for RPG Kingdom Supervisor diagnostics."""
from __future__ import annotations

import argparse
import json
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import diagnostics  # type: ignore  # noqa: E402
import unity_run_history  # type: ignore  # noqa: E402

PAGE = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RPG Kingdom Supervisor</title>
<style>
:root{color-scheme:dark;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}body{margin:0;background:#101113;color:#dedede}main{max-width:1600px;margin:auto;padding:20px}h1{font:650 24px system-ui;margin:0 0 14px}h2{font:650 16px system-ui;margin:0 0 10px}.bar,.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 14px}input,select,button{background:#1d2024;color:#eee;border:1px solid #41454b;border-radius:6px;padding:7px 9px}button{cursor:pointer}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;margin-bottom:16px}.card{background:#181a1e;border:1px solid #30343a;border-radius:8px;padding:12px;min-width:0}.wide{grid-column:1/-1}.row{display:grid;grid-template-columns:105px 1fr;gap:7px;margin:4px 0}.muted{color:#9197a1}.good{color:#9ccc65}.bad{color:#ff8a80}.warn{color:#ffd180}.running{color:#80cbc4}.pill{padding:2px 7px;border:1px solid #494e56;border-radius:999px;white-space:nowrap}table{width:100%;border-collapse:collapse;font-size:12px}th,td{text-align:left;border-bottom:1px solid #30343a;padding:8px 6px;vertical-align:top}th{color:#9ba1aa;font-weight:600;position:sticky;top:0;background:#181a1e}tbody tr{cursor:pointer}tbody tr:hover{background:#20242a}.scroll{overflow:auto;max-height:480px}pre{white-space:pre-wrap;word-break:break-word;font-size:11px;line-height:1.35;margin:6px 0}.detail{display:none}.detail.open{display:block}.diag-title{font-weight:700}.path{font-size:11px;word-break:break-all;color:#aab2bf}.test{padding:7px 0;border-bottom:1px solid #30343a}.status-passed{color:#9ccc65}.status-failed,.status-timed_out{color:#ff8a80}.status-running{color:#80cbc4}.status-rejected_busy{color:#ffd180}@media(max-width:700px){main{padding:12px}th:nth-child(5),td:nth-child(5),th:nth-child(6),td:nth-child(6){display:none}}
</style></head><body><main>
<h1>RPG Kingdom Supervisor Diagnostics</h1>
<div class="bar">GH-<input id="issue" value="97" size="5"><button onclick="refreshAll()">Refresh</button><span id="stamp" class="muted"></span></div>
<div id="summary" class="grid"></div>
<div class="card wide"><h2>Unity run history</h2>
<div class="filters"><select id="operation" onchange="loadRuns()"><option value="">all operations</option><option>health</option><option>editmode</option><option>playmode</option></select><select id="status" onchange="loadRuns()"><option value="">all statuses</option><option value="running">running</option><option value="passed">passed</option><option value="failed">failed</option><option value="timed_out">timed out</option><option value="rejected_busy">host busy</option></select></div>
<div class="scroll"><table><thead><tr><th>Status</th><th>Issue</th><th>Operation</th><th>Filter / diagnosis</th><th>Started</th><th>Completed</th><th>Duration</th><th>Exit</th><th>Run/request</th></tr></thead><tbody id="runs"></tbody></table></div></div>
<div id="detail" class="card wide detail" style="margin-top:12px"></div>
</main><script>
const esc=v=>String(v??'-').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtDuration=v=>v===null||v===undefined?'-':`${Math.round(Number(v)*10)/10}s`;
const statusText=r=>r.finalStatus==='timed_out'?'timed out':(r.finalStatus||r.status||'-').replaceAll('_',' ');
function rows(obj, defs){return defs.map(([k,l])=>`<div class="row"><span class="muted">${esc(l)}</span><span>${esc(obj?.[k])}</span></div>`).join('')}
async function loadSummary(){const n=document.getElementById('issue').value;const d=await fetch('/api/issue/'+encodeURIComponent(n),{cache:'no-store'}).then(r=>r.json());const g=d.github||{},w=d.workspace||{},s=d.symphony||{},c=d.codex||{},u=d.unity_lock||{};document.getElementById('summary').innerHTML=`<div class="card"><h2>GitHub</h2>${rows(g,[['state','state'],['labels','labels'],['comment_count','comments'],['updated_at','updated']])}</div><div class="card"><h2>Workspace</h2>${rows(w,[['branch','branch'],['head','head'],['attempt_complete','attempt used']])}<pre>${esc(w.git_status||'clean / unavailable')}</pre></div><div class="card"><h2>Symphony</h2>${rows(s,[['session_id','session'],['turn','turn'],['thread_id','thread']])}</div><div class="card"><h2>Unity resource</h2>${rows(u,[['held','lock held'],['owner','owner']])}</div><div class="card wide"><h2>Recent Symphony lines</h2><pre>${esc((s.lines||[]).slice(-12).join('\n')||'-')}</pre></div>`}
async function loadRuns(){const n=document.getElementById('issue').value;const qs=new URLSearchParams({issue:'GH-'+n,limit:'100'});const op=document.getElementById('operation').value,st=document.getElementById('status').value;if(op)qs.set('operation',op);if(st)qs.set('status',st);const runs=await fetch('/api/unity/runs?'+qs,{cache:'no-store'}).then(r=>r.json());document.getElementById('runs').innerHTML=(runs.items||[]).map(r=>`<tr onclick="openRun('${esc(r.requestId)}')"><td class="status-${esc(r.finalStatus)}"><span class="pill">${esc(statusText(r))}</span></td><td>${esc(r.issue)}</td><td>${esc(r.operation)}</td><td><div>${esc(r.testFilter||'(all)')}</div><div class="muted">${esc(r.diagnosis?.title||'-')}: ${esc(r.diagnosis?.message||'')}</div></td><td>${esc(r.startedAt||r.acceptedAt||'-')}</td><td>${esc(r.completedAt||'-')}</td><td>${esc(fmtDuration(r.durationSeconds))}</td><td>${esc(r.exitCode)}</td><td>${esc(r.requestId)}<div class="path">${esc(r.workspace||'')}</div><div class="path">${esc(r.artifactPath||'')}</div></td></tr>`).join('')||'<tr><td colspan="9" class="muted">No Unity runs found for this issue/filter.</td></tr>'}
async function openRun(id){const r=await fetch('/api/unity/run/'+encodeURIComponent(id),{cache:'no-store'});if(!r.ok)return;const d=await r.json(),p=d.paths||{},diag=d.diagnosis||{};const tests=(d.failedTests||[]).map(t=>`<div class="test"><b>${esc(t.name)}</b><pre>${esc(t.message)}</pre><pre class="muted">${esc(t.stackTrace)}</pre></div>`).join('')||'<span class="muted">none</span>';const paths=Object.entries(p).filter(([,v])=>v).map(([k,v])=>`<div class="row"><span class="muted">${esc(k)}</span><span class="path">${esc(v)}</span></div>`).join('');document.getElementById('detail').innerHTML=`<h2>Run ${esc(d.requestId)}</h2><div class="diag-title status-${esc(d.finalStatus)}">${esc(diag.title)}</div><pre>${esc(diag.message)}</pre>${diag.sourcePath?`<div class="path">${esc(diag.sourcePath)}:${esc(diag.line)}:${esc(diag.column)} — ${esc(diag.code)}</div>`:''}<div class="grid" style="margin-top:12px"><div class="card"><h2>Run</h2>${rows(d,[['issue','issue'],['workspace','workspace'],['operation','operation'],['testFilter','filter'],['startedAt','started'],['completedAt','completed'],['durationSeconds','seconds'],['exitCode','exit']])}</div><div class="card"><h2>Artifacts</h2>${paths||'<span class="muted">none</span>'}</div><div class="card wide"><h2>Failed tests</h2>${tests}</div><div class="card wide"><h2>Relevant excerpts</h2><pre>${esc((d.errorExcerpts||[]).join('\n')||'-')}</pre></div><div class="card wide"><h2>Summary</h2><pre>${esc(JSON.stringify(d.summary,null,2))}</pre></div><div class="card wide"><h2>Broker request / response</h2><pre>${esc(JSON.stringify(d.broker,null,2))}</pre></div></div>`;document.getElementById('detail').classList.add('open');document.getElementById('detail').scrollIntoView({behavior:'smooth',block:'start'})}
async function refreshAll(){try{await Promise.all([loadSummary(),loadRuns()]);document.getElementById('stamp').textContent='updated '+new Date().toLocaleTimeString()}catch(e){document.getElementById('stamp').textContent='error: '+e}}
refreshAll();setInterval(refreshAll,5000);
</script></body></html>'''


def normalize_issue(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().upper()
    if text.isdigit():
        return f"GH-{text}"
    return text if re.fullmatch(r"GH-\d+", text) else None


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def serve(port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def send_json(self, value: Any, status: int = 200) -> None:
            body = json_bytes(value)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
                self.send_json(diagnostics.collect(int(match.group(1))))
                return

            if parsed.path == "/api/unity/runs":
                query = parse_qs(parsed.query)
                issue = normalize_issue((query.get("issue") or [None])[0])
                operation = (query.get("operation") or [None])[0] or None
                status = (query.get("status") or [None])[0] or None
                try:
                    limit = int((query.get("limit") or ["100"])[0])
                except ValueError:
                    limit = 100
                items = unity_run_history.collect_runs(
                    issue=issue,
                    operation=operation,
                    status=status,
                    limit=limit,
                )
                self.send_json({"items": items, "count": len(items)})
                return

            match = re.fullmatch(r"/api/unity/run/([^/]+)", parsed.path)
            if match:
                request_id = unquote(match.group(1))
                run = unity_run_history.find_run(request_id)
                if run is None:
                    self.send_json({"error": "run not found"}, HTTPStatus.NOT_FOUND)
                else:
                    self.send_json(run)
                return

            self.send_error(HTTPStatus.NOT_FOUND)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"RPG Kingdom Supervisor dashboard: http://127.0.0.1:{port}")
    print("Ctrl+C to stop. The dashboard is read-only and bound to localhost.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="RPG Kingdom Supervisor read-only dashboard")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
