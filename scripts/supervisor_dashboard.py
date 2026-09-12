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
import supervisor_detail  # type: ignore  # noqa: E402
import supervisor_maintenance  # type: ignore  # noqa: E402
import supervisor_telemetry  # type: ignore  # noqa: E402
import supervisor_usage_analysis  # type: ignore  # noqa: E402
import unity_run_history  # type: ignore  # noqa: E402

PAGE_PATH = SCRIPT_DIR / "supervisor_dashboard.html"
PAGE = PAGE_PATH.read_text(encoding="utf-8")

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


def rendered_page() -> str:
    marker = "</body>"
    return PAGE.replace(marker, QUOTA_FRESHNESS_SCRIPT + marker, 1) if marker in PAGE else PAGE + QUOTA_FRESHNESS_SCRIPT


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
                    # Historical before/after quota stays on detail.worker. Current quota is attached
                    # under an explicitly global key so a later window reset cannot be mistaken for
                    # the worker lifetime's historical state.
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
