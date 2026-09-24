import importlib.util
from datetime import datetime
from pathlib import Path
P=Path(__file__).parents[1]/"scripts"/"autonomous-plan.py"
s=importlib.util.spec_from_file_location("ap",P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
B={"autonomous_window":{"enabled":True,"timezone":"America/Denver","start":"22:00","end":"06:00"},"quota":{"min_primary_to_start_turn":15,"min_weekly_to_start_turn":5},"work_plan":[{"issue":79},{"issue":138,"after":[79]}]}
def e(st,h=23):return m.evaluate(B,st,datetime.fromisoformat(f"2026-09-24T{h:02d}:00:00-06:00"))
assert e({"quota":{"primary_remaining":50,"weekly_remaining":50},"issues":{}})["issue"]==79
assert e({"quota":{},"issues":{}},12)["state"]=="waiting_for_window"
assert e({"quota":{"primary_remaining":10},"issues":{}})["state"]=="waiting_for_quota"
assert e({"quota":{},"issues":{"79":{"manual_action_required":True}}})["state"]=="human_gate"
assert e({"quota":{},"completed":[79],"issues":{}})["issue"]==138
assert e({"active_worker":True,"quota":{},"issues":{}})["state"]=="running"
print("autonomous-plan-test: PASS")
