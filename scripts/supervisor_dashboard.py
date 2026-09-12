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
QUOTA_UI_PATH = SCRIPT_DIR / "supervisor_quota_ui.js"
PAGE = PAGE_PATH.read_text(encoding="utf-8").replace(
    "</body>", '<script src="/supervisor-quota-ui.js"></script>\n</body>', 1
)
QUOTA_UI = QUOTA_UI_PATH.read_text(encoding="utf-8")


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

        def send_text(self, value: str, content_type: str) -> None:
            body = value.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.send_text(PAGE, "text/html; charset=utf-8")
                return

            if parsed.path == "/supervisor-quota-ui.js":
                self.send_text(QUOTA_UI, "application/javascript; charset=utf-8")
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
