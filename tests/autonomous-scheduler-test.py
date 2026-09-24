import importlib.util
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("sched", Path(__file__).parents[1] / "scripts" / "autonomous-scheduler.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    m.STATE_ROOT = root
    m.CONFIG = root / "plan.json"
    m.STATUS = root / "status.json"
    config = {
        "autonomous_window": {"enabled": True, "timezone": "UTC", "start": "00:00", "end": "00:00"},
        "quota": {"min_primary_to_start_turn": 15, "min_weekly_to_start_turn": 5},
        "work_plan": [{"issue": 79}],
    }
    m.atomic_write(m.CONFIG, config)
    assert m.read(m.CONFIG, {})["work_plan"][0]["issue"] == 79
    assert m.update_control("pause")["paused"] is True
    assert m.update_control("resume")["paused"] is False
    m.active_issue = lambda: 79
    assert m.update_control("stop-after-issue")["stop_after_issue"] == 79
    assert "stop_after_issue" not in m.update_control("resume")

    m.snapshot = lambda config, repo: {
        "active_worker": False,
        "active_issue": None,
        "completed": [],
        "quota": {"status": "available", "primary_remaining": 50, "weekly_remaining": 50},
        "issues": {"79": {"labels": []}},
    }
    calls = []
    m.mutate = lambda repo, decision, state: calls.append(decision["issue"]) or "armed"
    output = m.tick(m.read(m.CONFIG, {}), "owner/repo")
    assert output["decision"]["dispatch"] is True and calls == [79]
    assert m.read(m.STATUS, {})["actionResult"] == "armed"

    config = m.read(m.CONFIG, {})
    config["paused"] = True
    output = m.tick(config, "owner/repo")
    assert output["decision"]["state"] == "paused" and len(calls) == 1

print("autonomous-scheduler-test: PASS")
