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
import finished_tasks  # type: ignore  # noqa: E402
import review_state  # type: ignore  # noqa: E402
import supervisor_activity  # type: ignore  # noqa: E402
import supervisor_detail  # type: ignore  # noqa: E402
import supervisor_maintenance  # type: ignore  # noqa: E402
import supervisor_telemetry  # type: ignore  # noqa: E402
import supervisor_usage_analysis  # type: ignore  # noqa: E402
import unity_run_history  # type: ignore  # noqa: E402

PAGE_PATH = SCRIPT_DIR / "supervisor_dashboard.html"
PAGE = PAGE_PATH.read_text(encoding="utf-8")
FINISHED_TASKS_PATH = SCRIPT_DIR / "finished_tasks_dashboard.html"
FINISHED_TASKS_SCRIPT = FINISHED_TASKS_PATH.read_text(encoding="utf-8")

QUOTA_FRESHNESS_SCRIPT = r"""
<script>
(function(){
  const baseRenderOverview = renderOverview;
  const ageSeconds = value => {
    if (!value) return null;
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return null;
    return Math.max(0, (Date.now() - parsed.getTime()) / 1000);
  };
  const ageLabel = seconds => {
    if (seconds === null) return 'unknown age';
    if (seconds < 60) return `${Math.round(seconds)}s ago`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
    return `${Math.round(seconds / 3600)}h ago`;
  };
  const quotaPercent = value => value === null || value === undefined ? null : `${Math.round(Number(value) * 10) / 10}%`;
  const resetLabel = value => value ? new Date(value).toLocaleString() : null;
  function renderQuotaFreshness(){
    const q = state.operations?.quota || {};
    const rate = q.rateLimits || {};
    const primary = rate.primary || {};
    const secondary = rate.secondary || {};
    const primaryPct = quotaPercent(primary.remainingPercent);
    const weeklyPct = quotaPercent(secondary.remainingPercent);
    const age = ageSeconds(q.lastSuccessfulObservedAt || q.observedAt);
    const staleAfter = Number(q.staleAfterSeconds || 600);
    const staleByAge = age !== null && age > staleAfter;
    const latest = q.latestRefresh || {};
    const failedLatest = latest.status === 'unavailable';
    const unavailableReason = latest.reason || q.reason;
    const reset = resetLabel(primary.resetsAtIso);
    const header = document.getElementById('header-quota');
    const metric = document.getElementById('metric-quota');
    const note = document.getElementById('metric-quota-note');
    if (!header || !metric || !note) return;

    if (q.status === 'available' && !staleByAge) {
      header.innerHTML = `Quota <strong>${esc(primaryPct || '—')} / ${esc(weeklyPct || '—')}</strong>`;
      metric.textContent = primaryPct || 'available';
      metric.className = `metric-value ${Number(primary.remainingPercent) < 15 ? 'bad' : Number(primary.remainingPercent) < 35 ? 'warn' : 'good'}`;
      note.textContent = `weekly ${weeklyPct || '—'} remaining · sampled ${ageLabel(age)}${reset ? ` · 5h reset ${reset}` : ''}`;
      return;
    }

    if (q.status === 'stale' || (q.status === 'available' && staleByAge)) {
      header.innerHTML = `Quota <strong>stale · last ${esc(ageLabel(age))}</strong>`;
      metric.textContent = 'stale';
      metric.className = 'metric-value warn';
      const lastKnown = primaryPct || weeklyPct ? `last known ${primaryPct || '—'} / ${weeklyPct || '—'}` : 'last known percentages unavailable';
      note.textContent = `${lastKnown}${failedLatest && unavailableReason ? ` · latest refresh failed: ${unavailableReason}` : ''}${reset ? ` · 5h reset ${reset}` : ''}`;
      return;
    }

    if (!q.observedAt && !latest.observedAt) {
      header.innerHTML = 'Quota <strong>not sampled yet</strong>';
      metric.textContent = 'not sampled';
      metric.className = 'metric-value';
      note.textContent = q.reason || 'Waiting for the first host-owned App Server quota probe';
      return;
    }

    header.innerHTML = 'Quota <strong>unavailable</strong>';
    metric.textContent = 'unavailable';
    metric.className = 'metric-value warn';
    note.textContent = unavailableReason || 'Authoritative App Server quota probe is unavailable';
  }
  renderOverview = function(){
    baseRenderOverview();
    renderQuotaFreshness();
  };
  if (state.operations) renderQuotaFreshness();
})();
</script>
"""

TURN_HISTORY_SCRIPT = r"""
<script>
(function(){
  const baseOpenWorker = openWorker;
  const quotaValue = (turn, windowName) => {
    const value = turn?.quotaAfter?.rateLimits?.[windowName]?.remainingPercent;
    return value === null || value === undefined ? '—' : `${Math.round(Number(value) * 10) / 10}%`;
  };
  const tokenValue = (turn, key) => {
    const delta = turn?.tokenDelta || {};
    return delta.status === 'available' ? fmtNum(delta[key] || 0) : '—';
  };
  const yesNo = value => value === true ? 'yes' : value === false ? 'no' : '—';
  function turnHistoryBlock(turns){
    if (!turns.length) return '';
    const last = turns[turns.length - 1] || {};
    const rows = turns.map(turn => `<tr>
      <td>${esc(turn.turn)}</td>
      <td>${esc(fmtDuration(turn.durationSeconds))}</td>
      <td>${esc(turn.decision || '—')}</td>
      <td>${esc(tokenValue(turn, 'totalTokens'))}</td>
      <td>${esc(tokenValue(turn, 'cachedInputTokens'))}</td>
      <td>${esc(quotaValue(turn, 'primary'))}</td>
      <td>${esc(quotaValue(turn, 'secondary'))}</td>
      <td>${esc(yesNo(turn.progress?.workspaceChanged))}</td>
      <td>${esc(turn.unityAfter?.runId || '—')}</td>
      <td>${esc(turn.reason || '—')}</td>
    </tr>`).join('');
    return `<details class="details-box" open>
      <summary>Per-turn continuation evidence</summary>
      <div class="details-body">
        <div class="panel-sub" style="margin-bottom:10px">Hard cap ${esc(last.hardMaxTurns ?? '—')} · automatic route cap ${esc(last.automaticTurnLimit ?? '—')} · route ${esc(last.route || '—')}</div>
        <div class="scroll"><table><thead><tr><th>Turn</th><th>Duration</th><th>Decision</th><th>Total Δ</th><th>Cached Δ</th><th>5h after</th><th>Weekly after</th><th>Diff changed</th><th>Latest Unity</th><th>Reason</th></tr></thead><tbody>${rows}</tbody></table></div>
        <div class="usage-note">Token deltas keep cached input separate. Quota percentages are authoritative App Server snapshots and are never inferred from token counts.</div>
      </div>
    </details>`;
  }
  openWorker = async function(id){
    await baseOpenWorker(id);
    try {
      const response = await fetch('/api/worker/' + encodeURIComponent(id), {cache:'no-store'});
      if (!response.ok) return;
      const detail = await response.json();
      const block = turnHistoryBlock(detail.turnHistory || []);
      const content = document.getElementById('detail-content');
      if (content && block) content.insertAdjacentHTML('beforeend', block);
    } catch (_) {
      // The base worker drawer remains usable when turn-history telemetry is unavailable.
    }
  };
})();
</script>
"""

COMPACT_LAYOUT_STYLE = r"""
<style id="compact-operator-layout">
@media (max-width:560px){
  body{font-size:12px}
  header{backdrop-filter:blur(6px)}
  .header-inner{display:block;padding:6px 8px}
  .brand{display:none}
  .header-status{display:flex;flex-wrap:nowrap;justify-content:flex-start;gap:4px;overflow-x:auto;scrollbar-width:none;-webkit-overflow-scrolling:touch}
  .header-status::-webkit-scrollbar,.tabs::-webkit-scrollbar{display:none}
  .header-chip{flex:0 0 auto;max-width:132px;padding:3px 6px;font-size:10px;overflow:hidden;text-overflow:ellipsis}
  #stamp{display:none}
  #refresh-button{flex:0 0 auto;padding:4px 7px;font-size:11px;position:sticky;right:0;background:#222a35}
  main{padding:8px 8px 28px}
  .tabs{display:flex;flex-wrap:nowrap;gap:4px;overflow-x:auto;scrollbar-width:none;-webkit-overflow-scrolling:touch;scroll-snap-type:x proximity;margin-bottom:8px}
  .tab{flex:0 0 auto;scroll-snap-align:start;padding:5px 8px;font-size:11px}
  .section-head{align-items:center;margin:0 0 6px}
  .section-head h2{font-size:14px}
  .section-head p{display:none}
  .section-head button{padding:5px 7px;font-size:11px}
  .overview-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;margin-bottom:8px}
  .hero-card,.hero-card.wide{grid-column:span 1;padding:8px;border-radius:8px}
  .hero-label{font-size:9px;letter-spacing:.035em}
  .metric-value{font-size:18px;margin-top:2px}
  .metric-note{font-size:10px;margin-top:3px;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
  .banner{padding:8px 9px;margin-bottom:8px}
  .service-strip{grid-template-columns:repeat(2,minmax(0,1fr));gap:5px;margin-bottom:8px}
  .service{padding:7px 8px;border-radius:8px}
  .service-name{font-size:11px}
  .service-meta{display:none}
  .pill{padding:2px 5px;font-size:9px}
  .panel{padding:8px;margin-bottom:8px;border-radius:8px}
  .panel h3{font-size:13px;margin-bottom:6px}
  .compact-list{gap:5px}
  .worker-summary{grid-template-columns:minmax(0,1fr) auto;gap:3px 8px;padding:8px;border-radius:7px}
  .worker-summary strong{font-size:12px}
  .worker-summary .small{font-size:10px}
  .worker-summary>:nth-child(n+3){font-size:10px;text-align:right}
  .queue-grid{grid-template-columns:1fr;gap:5px}
  .queue{padding:8px;border-radius:8px}
  .queue.empty{display:none}
  .queue-item{padding:7px 0}
  .queue-title,.queue-item-title{font-size:12px}
  .meta{font-size:10px}
  .timeline-item{grid-template-columns:minmax(0,1fr) auto;gap:2px 6px;padding:8px 0}
  .timeline-category{grid-column:1;grid-row:1;font-size:9px}
  .timeline-time{grid-column:2;grid-row:1;font-size:9px;text-align:right}
  .timeline-item>div:last-child{grid-column:1/-1}
  .timeline-title{font-size:12px}
  .details-box{margin-bottom:8px;border-radius:8px}
  details>summary{padding:8px 10px;font-size:12px}
  .details-body{padding:0 9px 9px}
  .summary-metrics,.analysis-grid,.issue-grid{grid-template-columns:1fr;gap:6px}
  .mini-metric{padding:8px}
  .mini-metric b{font-size:16px}
  .issue-shell{grid-template-columns:1fr;gap:8px}
  .issue-nav{position:static;padding:8px;border-radius:8px}
  .kv{grid-template-columns:1fr;gap:2px}
  .scroll{max-height:none}
  th,td{padding:6px 5px;font-size:10px}
  .detail-drawer.open{grid-template-columns:1fr}
  .detail-backdrop{display:none}
  .detail-panel{height:100dvh;padding:10px;border-left:0}
  .detail-panel-head{position:sticky;top:0;z-index:2;background:#10151c;padding:4px 0 8px;margin-bottom:8px}
  .detail-panel-head h2{font-size:14px}
  .pre{max-height:50vh;padding:8px;font-size:10px}
  .toolbar{gap:5px;margin-bottom:7px}
  button,input,select{min-height:32px}
}
@media (max-width:390px){
  .header-chip{max-width:108px}
  .service-strip{grid-template-columns:1fr}
  .service{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:6px}
  .overview-grid{gap:5px}
  .hero-card,.hero-card.wide{padding:7px}
  .metric-value{font-size:17px}
  .tab{padding-inline:7px}
}
</style>
"""


def rendered_page() -> str:
    marker = "</body>"
    extras = COMPACT_LAYOUT_STYLE + QUOTA_FRESHNESS_SCRIPT + TURN_HISTORY_SCRIPT + FINISHED_TASKS_SCRIPT
    return PAGE.replace(marker, extras + marker, 1) if marker in PAGE else PAGE + extras


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
                body = rendered_page().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if parsed.path == "/api/operations":
                self.send_json(supervisor_telemetry.collect_operations())
                return

            if parsed.path == "/api/maintenance":
                self.send_json(supervisor_maintenance.status())
                return

            if parsed.path == "/api/lifecycle":
                self.send_json(supervisor_activity.collect())
                return

            if parsed.path == "/api/finished-tasks":
                self.send_json(finished_tasks.collect())
                return

            if parsed.path == "/api/usage-analysis":
                query = parse_qs(parsed.query)
                try:
                    limit = int((query.get("limit") or ["500"])[0])
                except ValueError:
                    limit = 500
                self.send_json(supervisor_usage_analysis.analyze(limit=max(1, min(limit, 5000))))
                return

            match = re.fullmatch(r"/api/worker/([^/]+)", parsed.path)
            if match:
                run_id = unquote(match.group(1))
                detail = supervisor_detail.collect(run_id)
                if detail is None:
                    self.send_json({"error": "worker run not found"}, HTTPStatus.NOT_FOUND)
                else:
                    lifecycle = dict(detail.get("currentLifecycle") or {})
                    lifecycle["currentGlobalQuota"] = supervisor_telemetry.current_quota()
                    detail["currentLifecycle"] = lifecycle
                    self.send_json(detail)
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
