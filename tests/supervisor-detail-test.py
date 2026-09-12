#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import supervisor_detail  # noqa: E402

worker = {
    "runId":"GH-46-repair-r2","issue":46,"identifier":"GH-46","role":"repair",
    "model":"gpt-5.6-terra","effort":"medium","route":"terra",
    "startedAt":"2026-09-12T01:00:00+00:00","endedAt":"2026-09-12T01:10:00+00:00",
    "outcome":"agent-review","tokenUsage":{"status":"available","totalTokens":1234},
}
workers = [
    {**worker,"runId":"GH-46-implementation-r1","role":"implementation","startedAt":"2026-09-12T00:00:00+00:00","endedAt":"2026-09-12T00:30:00+00:00"},
    worker,
    {**worker,"runId":"GH-46-review-r3","role":"review","startedAt":"2026-09-12T01:20:00+00:00","endedAt":"2026-09-12T01:25:00+00:00"},
]

supervisor_detail._load_worker = lambda run_id: (worker, "completed")
supervisor_detail._all_issue_workers = lambda issue: workers
supervisor_detail.supervisor_activity.collect = lambda **kwargs: {
    "items":[{"issue":46,"lifecycleState":"human_review","prNumber":55,"headSha":"abc123"}],
    "activity":[
        {"issue":46,"category":"lifecycle","observedAt":"2026-09-12T01:05:00+00:00","title":"Moved to Rework"},
        {"issue":46,"category":"review","observedAt":"2026-09-12T01:09:00+00:00","title":"Automated review cycle 2: approved"},
        {"issue":46,"category":"worker","observedAt":"2026-09-12T01:10:00+00:00","workerRunId":"GH-46-repair-r2","title":"Repair completed"},
    ],
}
supervisor_detail._unity_for_worker = lambda current, root: [{
    "requestId":"unity-2","issue":"GH-46","operation":"playmode","finalStatus":"passed",
    "artifactPath":"/tmp/unity-2","paths":{"resultsXml":"/tmp/unity-2/results.xml"},
}]
supervisor_detail._git_for_worker = lambda current, root: [{
    "id":"git-2","issue":46,"observedAt":"2026-09-12T01:08:00+00:00","title":"Git handoff completed","prNumber":55,
}]
supervisor_detail.supervisor_maintenance.status = lambda: {"retentionDays":30,"observedAt":"2026-09-12T00:00:00+00:00"}

value = supervisor_detail.collect("GH-46-repair-r2", workspace_root=Path("/tmp/workspaces"), use_cache=False)
assert value is not None
assert value["workerState"] == "completed"
assert value["worker"]["tokenUsage"]["totalTokens"] == 1234
assert value["currentLifecycle"]["lifecycleState"] == "human_review"
assert value["continuationLineage"]["previousRunId"] == "GH-46-implementation-r1"
assert value["continuationLineage"]["nextRunId"] == "GH-46-review-r3"
assert value["continuationLineage"]["sequence"] == 2
assert value["unityRuns"][0]["requestId"] == "unity-2"
assert value["gitHandoffs"][0]["prNumber"] == 55
assert any(item["kind"] == "resultsXml" for item in value["artifacts"])
assert len(value["reviewHistory"]) == 1
assert len(value["activity"]) == 3

print("supervisor-detail-test: PASS")
