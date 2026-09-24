#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from datetime import datetime,time
from pathlib import Path
from zoneinfo import ZoneInfo
HUMAN={"symphony:human-review","symphony:human-attention"}
RECOVERABLE={"continuation_policy","usage_limit_exceeded","resource_unavailable","preflight_recoverable"}
def in_window(now,start,end):
 s=time.fromisoformat(start); e=time.fromisoformat(end); t=now.timetz().replace(tzinfo=None)
 return True if s==e else (s<=t<e if s<e else t>=s or t<e)
def evaluate(config,state,now):
 w=config.get("autonomous_window",{})
 if not w.get("enabled",False): return {"state":"disabled","dispatch":False}
 if not in_window(now.astimezone(ZoneInfo(w["timezone"])),w["start"],w["end"]): return {"state":"waiting_for_window","dispatch":False}
 if state.get("active_worker"): return {"state":"running","dispatch":False}
 completed={int(x) for x in state.get("completed",[])}
 explicit_blocked={int(x) for x in state.get("blocked",[])}
 human_blocked=[]; dependency_blocked=[]
 for item in config.get("work_plan",[]):
  issue=int(item["issue"])
  if not item.get("enabled",True) or issue in completed or issue in explicit_blocked: continue
  deps={int(x) for x in item.get("after",[])}
  if not deps.issubset(completed):
   dependency_blocked.append(issue); continue
  life=state.get("issues",{}).get(str(issue),{})
  labels={str(x).lower() for x in life.get("labels",[])}
  halt=life.get("halt_kind")
  if labels&HUMAN or life.get("manual_action_required") or (halt and halt not in RECOVERABLE):
   human_blocked.append(issue); continue
  q=state.get("quota",{}); floors=config.get("quota",{})
  if q.get("status")!="available":
   return {"state":"waiting_for_quota","dispatch":False,"issue":issue,"reason":"authoritative quota unavailable","blocked":human_blocked}
  if q.get("primary_remaining",0)<floors.get("min_primary_to_start_turn",0) or q.get("weekly_remaining",0)<floors.get("min_weekly_to_start_turn",0):
   return {"state":"waiting_for_quota","dispatch":False,"issue":issue,"blocked":human_blocked}
  return {"state":"eligible","dispatch":True,"issue":issue,"action":item.get("action","implement"),"blocked":human_blocked}
 if human_blocked: return {"state":"human_gate","dispatch":False,"blocked":human_blocked}
 return {"state":"complete_or_blocked","dispatch":False,"dependencyBlocked":dependency_blocked}
def main():
 p=argparse.ArgumentParser(); p.add_argument("--config",required=True); p.add_argument("--state",required=True); p.add_argument("--now"); a=p.parse_args()
 now=datetime.fromisoformat(a.now) if a.now else datetime.now().astimezone()
 print(json.dumps(evaluate(json.loads(Path(a.config).read_text()),json.loads(Path(a.state).read_text()),now),sort_keys=True))
if __name__=="__main__": main()
