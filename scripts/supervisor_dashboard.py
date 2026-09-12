#!/usr/bin/env python3
"""Read-only localhost operations dashboard for RPG Kingdom Supervisor."""
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
import review_state  # type: ignore  # noqa: E402
import supervisor_activity  # type: ignore  # noqa: E402
import supervisor_telemetry  # type: ignore  # noqa: E402
import unity_run_history  # type: ignore  # noqa: E402

PAGE = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RPG Kingdom Supervisor</title>
<style>
:root{color-scheme:dark;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}body{margin:0;background:#101113;color:#dedede}main{max-width:1600px;margin:auto;padding:20px}h1{font:650 24px system-ui;margin:0 0 14px}h2{font:650 16px system-ui;margin:0 0 10px}h3{font:650 13px system-ui;margin:0 0 8px}.bar,.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 14px}input,select,button{background:#1d2024;color:#eee;border:1px solid #41454b;border-radius:6px;padding:7px 9px}button{cursor:pointer}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;margin-bottom:16px}.queue-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px}.card{background:#181a1e;border:1px solid #30343a;border-radius:8px;padding:12px;min-width:0}.wide{grid-column:1/-1}.queue-human_review,.queue-human_attention,.queue-halted,.queue-report_complete{border-color:#6a5736}.queue-human_attention,.queue-halted{border-color:#7b4545}.row{display:grid;grid-template-columns:125px 1fr;gap:7px;margin:4px 0}.muted{color:#9197a1}.good,.health-healthy{color:#9ccc65}.bad,.health-stopped{color:#ff8a80}.warn,.health-degraded,.health-blocked{color:#ffd180}.running,.health-busy{color:#80cbc4}.health-unknown{color:#b0bec5}.pill{padding:2px 7px;border:1px solid #494e56;border-radius:999px;white-space:nowrap}.queue-item{padding:9px 0;border-top:1px solid #30343a}.queue-item:first-of-type{border-top:0}.queue-item a,.timeline a{color:#9ecbff;text-decoration:none}.queue-item a:hover,.timeline a:hover{text-decoration:underline}.meta{font-size:11px;color:#9aa1aa;line-height:1.5}.timeline-item{display:grid;grid-template-columns:155px 92px 1fr;gap:10px;padding:9px 0;border-bottom:1px solid #30343a}.timeline-item:last-child{border-bottom:0}.timeline-title{font-weight:650}.timeline-category{text-transform:uppercase;font-size:10px;letter-spacing:.04em;color:#aab2bf}.action{color:#ffd180;font-weight:650}table{width:100%;border-collapse:collapse;font-size:12px}th,td{text-align:left;border-bottom:1px solid #30343a;padding:8px 6px;vertical-align:top}th{color:#9ba1aa;font-weight:600;position:sticky;top:0;background:#181a1e}tbody tr{cursor:pointer}tbody tr:hover{background:#20242a}.scroll{overflow:auto;max-height:520px}pre{white-space:pre-wrap;word-break:break-word;font-size:11px;line-height:1.35;margin:6px 0}.detail{display:none}.detail.open{display:block}.diag-title{font-weight:700}.path{font-size:11px;word-break:break-all;color:#aab2bf}.test{padding:7px 0;border-bottom:1px solid #30343a}.status-passed{color:#9ccc65}.status-failed,.status-timed_out,.status-stalled{color:#ff8a80}.status-running{color:#80cbc4}.status-rejected_busy,.status-blocked{color:#ffd180}@media(max-width:800px){main{padding:12px}.timeline-item{grid-template-columns:1fr}.timeline-category{margin-top:-5px}th:nth-child(6),td:nth-child(6),th:nth-child(7),td:nth-child(7){display:none}}
</style></head><body><main>
<h1>RPG Kingdom Supervisor Operations</h1>
<div class="bar"><button onclick="refreshAll()">Refresh all</button><span id="stamp" class="muted"></span></div>
<div id="operations" class="grid"></div>
<div class="card wide" style="margin-bottom:16px"><h2>Work lifecycle queues</h2><div id="lifecycle-status" class="muted"></div><div id="queues" class="queue-grid" style="margin-top:10px"></div></div>
<div class="card wide" style="margin-bottom:16px"><h2>Recent activity</h2><div id="activity" class="timeline scroll"></div></div>
<div class="card wide" style="margin-bottom:16px"><h2>Recent Codex worker lifetimes</h2><div class="scroll"><table><thead><tr><th>Issue</th><th>Role</th><th>Model / effort</th><th>Outcome</th><th>Tokens</th><th>5h delta</th><th>Weekly delta</th><th>Duration</th></tr></thead><tbody id="workers"></tbody></table></div></div>
<div class="bar">Issue detail: GH-<input id="issue" value="97" size="5"><button onclick="loadIssueViews()">Refresh issue</button></div>
<div id="summary" class="grid"></div>
<div class="card wide"><h2>Unity run history</h2>
<div class="filters"><select id="operation" onchange="loadRuns()"><option value="">all operations</option><option>health</option><option>editmode</option><option>playmode</option></select><select id="status" onchange="loadRuns()"><option value="">all statuses</option><option value="running">running</option><option value="passed">passed</option><option value="failed">failed</option><option value="stalled">stalled</option><option value="blocked">stall recovery blocked</option><option value="timed_out">timed out</option><option value="rejected_busy">host busy</option></select></div>
<div class="scroll"><table><thead><tr><th>Status</th><th>Issue</th><th>Operation</th><th>Filter / diagnosis</th><th>Started</th><th>Completed</th><th>Duration</th><th>Exit</th><th>Run/request</th></tr></thead><tbody id="runs"></tbody></table></div></div>
<div id="detail" class="card wide detail" style="margin-top:12px"></div>
</main><script>
const esc=v=>String(v??'-').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtDuration=v=>v===null||v===undefined?'-':`${Math.round(Number(v)*10)/10}s`;
const fmtAge=v=>{if(v===null||v===undefined)return'-';let s=Math.max(0,Number(v));if(s<60)return`${Math.round(s)}s`;if(s<3600)return`${Math.round(s/60)}m`;if(s<86400)return`${Math.round(s/3600)}h`;return`${Math.round(s/86400)}d`};
const fmtTime=v=>{if(!v)return'-';const d=new Date(v);return Number.isNaN(d.getTime())?String(v):d.toLocaleString()};
const statusText=r=>r.finalStatus==='timed_out'?'timed out':(r.finalStatus||r.status||'-').replaceAll('_',' ');
const progressText=r=>{const phase=r.phase||r.recovery?.activeRequest?.phase;if(!phase)return'';const last=r.lastProgressAt||r.recovery?.activeRequest?.lastProgressAt||'-';const idle=r.noProgressSeconds??r.recovery?.activeRequest?.noProgressSeconds;return`<div class="muted">phase ${esc(phase)} · last progress ${esc(last)}${idle===null||idle===undefined?'':` · idle ${esc(idle)}s`}</div>`};
const pct=v=>v===null||v===undefined?'-':`${v}%`;
const delta=v=>v===null||v===undefined?'-':`${v>0?'+':''}${v} pp`;
function rows(obj,defs){return defs.map(([k,l])=>`<div class="row"><span class="muted">${esc(l)}</span><span>${esc(obj?.[k])}</span></div>`).join('')}
function serviceCard(name,s){const active=s.activeRequest||{};return`<div class="card"><h2>${esc(name)}</h2><div class="health-${esc(s.health)}"><span class="pill">${esc(s.health||'unknown')}</span></div>${rows(s,[['state','state'],['pid','PID'],['startedAt','started'],['updatedAt','updated'],['reason','reason']])}${active.issue?`<div class="row"><span class="muted">active</span><span>${esc(active.issue)} · ${esc(active.operation||'-')}</span></div>`:''}</div>`}
function workerTokens(w){const u=w.tokenUsage||{};return u.status==='available'?`${esc(u.totalTokens||0)} total · ${esc(u.cachedInputTokens||0)} cached`:'unavailable'}
function workerDelta(w,key){const q=w.quotaDelta||{},v=q[key]||{};return q.status==='available'?delta(v.remainingPercentagePointDelta):'-'}
async function loadOperations(){const d=await fetch('/api/operations',{cache:'no-store'}).then(r=>r.json());const s=d.services||{},q=d.quota||{},rate=q.rateLimits||{},p=rate.primary||{},w=rate.secondary||{};let cards=['symphony','review','unity','git'].map(k=>serviceCard(k,s[k]||{})).join('');cards+=`<div class="card"><h2>Codex quota</h2>${rows(q,[['status','status'],['observedAt','sampled']])}<div class="row"><span class="muted">5h used / left</span><span>${esc(pct(p.usedPercent))} / ${esc(pct(p.remainingPercent))}</span></div><div class="row"><span class="muted">5h reset</span><span>${esc(p.resetsAtIso||'-')}</span></div><div class="row"><span class="muted">weekly used / left</span><span>${esc(pct(w.usedPercent))} / ${esc(pct(w.remainingPercent))}</span></div><div class="row"><span class="muted">weekly reset</span><span>${esc(w.resetsAtIso||'-')}</span></div>${q.reason?`<pre class="warn">${esc(q.reason)}</pre>`:''}</div>`;const active=d.activeWorkers||[];cards+=`<div class="card"><h2>Active Codex slots</h2>${active.length?active.map(a=>`<div class="test"><b>${esc(a.identifier)} · ${esc(a.role)}</b><div>${esc(a.model)} / ${esc(a.effort)}</div><div class="muted">${a.alive?'live':'stale'} · ${esc(a.startedAt)}</div></div>`).join(''):'<span class="muted">none</span>'}</div>`;document.getElementById('operations').innerHTML=cards;document.getElementById('workers').innerHTML=(d.recentWorkers||[]).map(r=>`<tr><td>${esc(r.identifier)}</td><td>${esc(r.role)}</td><td>${esc(r.model)} / ${esc(r.effort)}</td><td>${esc(r.outcome)}</td><td>${esc(workerTokens(r))}</td><td>${esc(workerDelta(r,'primary'))}</td><td>${esc(workerDelta(r,'secondary'))}</td><td>${esc(fmtDuration(r.durationSeconds))}</td></tr>`).join('')||'<tr><td colspan="8" class="muted">No completed worker telemetry yet.</td></tr>'}
const queueLabels={implementing:'Implementing',agent_review:'Agent Review',rework:'Rework',human_review:'Human Review',human_attention:'Human Attention',halted:'Halted / Quota',report_complete:'Report Complete'};
function routeText(i){const r=i.route||{};if(r.status==='invalid')return`route invalid: ${esc(r.reason)}`;return`${esc(r.model||'-')} / ${esc(r.effort||'-')}`}
function queueItem(i){const pr=i.prNumber?` · <a href="${esc(i.prUrl)}" target="_blank" rel="noreferrer">PR #${esc(i.prNumber)}</a>`:'';const cycle=i.reviewCycle?` · review ${esc(i.reviewCycle)}`:'';const repairs=i.repairAttempts!==null&&i.repairAttempts!==undefined?` · repairs ${esc(i.repairAttempts)}/${esc(i.maxRepairAttempts)}`:'';const verdict=i.latestVerdict?`<div class="meta">verdict: ${esc(i.latestVerdict)}${i.latestSummary?' · '+esc(i.latestSummary):''}</div>`:'';const reason=i.haltReason?`<div class="meta ${i.quotaBlocked?'warn':''}">reason: ${esc(i.haltReason)}</div>`:'';const head=i.headSha?` · ${esc(String(i.headSha).slice(0,12))}`:'';return`<div class="queue-item"><div><a href="${esc(i.url)}" target="_blank" rel="noreferrer"><b>${esc(i.identifier)}</b></a> ${esc(i.title)}${pr}</div><div class="meta">${i.active?'active · ':'queued · '}${routeText(i)}${cycle}${repairs}${head} · ${fmtAge(i.stateAgeSeconds)} in state</div>${verdict}${reason}${i.humanActionRequired?'<div class="meta action">human action required</div>':''}</div>`}
function renderQueues(d){const q=d.queues||{};document.getElementById('queues').innerHTML=Object.keys(queueLabels).map(name=>{const items=q[name]||[];return`<div class="card queue-${name}"><h3>${queueLabels[name]} <span class="pill">${items.length}</span></h3>${items.length?items.map(queueItem).join(''):'<div class="muted">none</div>'}</div>`}).join('');const errors=d.errors||[];document.getElementById('lifecycle-status').innerHTML=d.available?(errors.length?`GitHub lifecycle available with ${esc(errors.length)} partial error(s).`:`GitHub lifecycle authority · refreshed ${esc(fmtTime(d.generatedAt))}`):`<span class="warn">GitHub lifecycle unavailable${errors.length?': '+esc(errors.join('; ')):''}</span>`}
function activityLinks(e){let links=[];if(e.issueUrl)links.push(`<a href="${esc(e.issueUrl)}" target="_blank" rel="noreferrer">${esc(e.identifier||('GH-'+e.issue))}</a>`);else if(e.identifier)links.push(esc(e.identifier));if(e.prNumber&&e.prUrl)links.push(`<a href="${esc(e.prUrl)}" target="_blank" rel="noreferrer">PR #${esc(e.prNumber)}</a>`);if(e.unityRequestId)links.push(`<a href="#" onclick="openRun('${esc(e.unityRequestId)}');return false">Unity ${esc(e.unityRequestId)}</a>`);for(const id of (e.unityRequestIds||[]))links.push(`<a href="#" onclick="openRun('${esc(id)}');return false">Unity ${esc(id)}</a>`);return links.join(' · ')}
function renderActivity(d){const items=d.activity||[];document.getElementById('activity').innerHTML=items.length?items.map(e=>`<div class="timeline-item"><div class="muted">${esc(fmtTime(e.observedAt))}</div><div class="timeline-category">${esc(e.category||'-')}</div><div><div class="timeline-title">${esc(e.title||e.kind||'-')}</div>${e.summary?`<div class="meta">${esc(e.summary)}</div>`:''}<div class="meta">${activityLinks(e)}</div></div></div>`).join(''):'<div class="muted">No recent cross-system activity.</div>'}
async function loadLifecycle(){const d=await fetch('/api/lifecycle',{cache:'no-store'}).then(r=>r.json());renderQueues(d);renderActivity(d)}
async function loadSummary(){const n=document.getElementById('issue').value;const d=await fetch('/api/issue/'+encodeURIComponent(n),{cache:'no-store'}).then(r=>r.json());const g=d.github||{},w=d.workspace||{},s=d.symphony||{},u=d.unity_lock||{},rv=d.review||{};document.getElementById('summary').innerHTML=`<div class="card"><h2>GitHub</h2>${rows(g,[['state','state'],['labels','labels'],['comment_count','comments'],['updated_at','updated']])}</div><div class="card"><h2>Review lifecycle</h2>${rows(rv,[['state','state'],['reviewCycle','review cycle'],['repairAttempts','repairs used'],['maxRepairAttempts','repair max'],['lastVerdict','last verdict'],['reason','halt reason'],['routingRecommendation','repair route'],['prNumber','PR'],['humanActionRequired','human action']])}<pre>${esc(rv.lastSummary||'-')}</pre></div><div class="card"><h2>Workspace</h2>${rows(w,[['branch','branch'],['head','head'],['attempt_complete','attempt used']])}<pre>${esc(w.git_status||'clean / unavailable')}</pre></div><div class="card"><h2>Symphony issue session</h2>${rows(s,[['session_id','session'],['turn','turn'],['thread_id','thread']])}</div><div class="card"><h2>Unity resource</h2>${rows(u,[['held','lock held'],['owner','owner']])}</div><div class="card wide"><h2>Recent Symphony lines</h2><pre>${esc((s.lines||[]).slice(-12).join('\n')||'-')}</pre></div>`}
async function loadRuns(){const n=document.getElementById('issue').value;const qs=new URLSearchParams({issue:'GH-'+n,limit:'100'});const op=document.getElementById('operation').value,st=document.getElementById('status').value;if(op)qs.set('operation',op);if(st)qs.set('status',st);const runs=await fetch('/api/unity/runs?'+qs,{cache:'no-store'}).then(r=>r.json());document.getElementById('runs').innerHTML=(runs.items||[]).map(r=>`<tr onclick="openRun('${esc(r.requestId)}')"><td class="status-${esc(r.finalStatus)}"><span class="pill">${esc(statusText(r))}</span></td><td>${esc(r.issue)}</td><td>${esc(r.operation)}</td><td><div>${esc(r.testFilter||'(all)')}</div><div class="muted">${esc(r.diagnosis?.title||'-')}: ${esc(r.diagnosis?.message||'')}</div>${progressText(r)}</td><td>${esc(r.startedAt||r.acceptedAt||'-')}</td><td>${esc(r.completedAt||'-')}</td><td>${esc(fmtDuration(r.durationSeconds))}</td><td>${esc(r.exitCode)}</td><td>${esc(r.requestId)}<div class="path">${esc(r.workspace||'')}</div><div class="path">${esc(r.artifactPath||'')}</div></td></tr>`).join('')||'<tr><td colspan="9" class="muted">No Unity runs found for this issue/filter.</td></tr>'}
async function openRun(id){const r=await fetch('/api/unity/run/'+encodeURIComponent(id),{cache:'no-store'});if(!r.ok)return;const d=await r.json(),p=d.paths||{},diag=d.diagnosis||{};const tests=(d.failedTests||[]).map(t=>`<div class="test"><b>${esc(t.name)}</b><pre>${esc(t.message)}</pre><pre class="muted">${esc(t.stackTrace)}</pre></div>`).join('')||'<span class="muted">none</span>';const paths=Object.entries(p).filter(([,v])=>v).map(([k,v])=>`<div class="row"><span class="muted">${esc(k)}</span><span class="path">${esc(v)}</span></div>`).join('');document.getElementById('detail').innerHTML=`<h2>Run ${esc(d.requestId)}</h2><div class="diag-title status-${esc(d.finalStatus)}">${esc(diag.title)}</div><pre>${esc(diag.message)}</pre>${diag.sourcePath?`<div class="path">${esc(diag.sourcePath)}:${esc(diag.line)}:${esc(diag.column)} — ${esc(diag.code)}</div>`:''}<div class="grid" style="margin-top:12px"><div class="card"><h2>Run</h2>${rows(d,[['issue','issue'],['workspace','workspace'],['operation','operation'],['testFilter','filter'],['startedAt','started'],['completedAt','completed'],['durationSeconds','seconds'],['phase','phase'],['lastProgressAt','last progress'],['noProgressSeconds','idle seconds'],['unityPid','Unity PID'],['exitCode','exit']])}</div><div class="card"><h2>Artifacts</h2>${paths||'<span class="muted">none</span>'}</div><div class="card wide"><h2>Stall / recovery</h2><pre>${esc(JSON.stringify(d.recovery,null,2))}</pre></div><div class="card wide"><h2>Failed tests</h2>${tests}</div><div class="card wide"><h2>Relevant excerpts</h2><pre>${esc((d.errorExcerpts||[]).join('\n')||'-')}</pre></div><div class="card wide"><h2>Summary</h2><pre>${esc(JSON.stringify(d.summary,null,2))}</pre></div><div class="card wide"><h2>Broker request / response</h2><pre>${esc(JSON.stringify(d.broker,null,2))}</pre></div></div>`;document.getElementById('detail').classList.add('open');document.getElementById('detail').scrollIntoView({behavior:'smooth',block:'start'})}
async function loadIssueViews(){await Promise.all([loadSummary(),loadRuns()])}
async function refreshAll(){try{await Promise.all([loadOperations(),loadLifecycle(),loadIssueViews()]);document.getElementById('stamp').textContent='updated '+new Date().toLocaleTimeString()}catch(e){document.getElementById('stamp').textContent='error: '+e}}
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

            if parsed.path == "/api/operations":
                self.send_json(supervisor_telemetry.collect_operations())
                return

            if parsed.path == "/api/lifecycle":
                self.send_json(supervisor_activity.collect())
                return

            match = re.fullmatch(r"/api/issue/(\d+)", parsed.path)
            if match:
                number = int(match.group(1))
                data = diagnostics.collect(number)
                data["review"] = review_state.collect(number)
                self.send_json(data)
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
                items = unity_run_history.collect_runs(issue=issue, operation=operation, status=status, limit=limit)
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
    parser = argparse.ArgumentParser(description="RPG Kingdom Supervisor read-only operations dashboard")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
