#!/usr/bin/env python3
"""Host-owned, model-free scheduler for approved autonomous work plans."""
from __future__ import annotations
import argparse, importlib.util, json, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("autonomous_plan",HERE/"autonomous-plan.py")
plan=importlib.util.module_from_spec(spec); spec.loader.exec_module(plan)
DEFAULT_REPO=os.environ.get("RPGK_REPO","Shashakar/RPG-Kingdom")
STATE_ROOT=Path(os.path.expanduser(os.environ.get("RPGK_SUPERVISOR_STATE_ROOT","~/.local/state/rpg-kingdom-supervisor")))
CONFIG=STATE_ROOT/"autonomous-plan.json"; STATUS=STATE_ROOT/"autonomous-status.json"

def atomic_write(path:Path,value:dict)->None:
 path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp")
 tmp.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n",encoding="utf-8"); tmp.replace(path)

def read(path:Path,default:dict)->dict:
 try:return json.loads(path.read_text(encoding="utf-8"))
 except (FileNotFoundError,json.JSONDecodeError):return default

def gh(args:list[str])->Any:
 p=subprocess.run(["gh",*args],text=True,capture_output=True,timeout=30)
 if p.returncode: raise RuntimeError((p.stderr or p.stdout).strip())
 return json.loads(p.stdout) if p.stdout.strip().startswith(("{","[")) else p.stdout.strip()

def issue_state(repo:str,number:int)->dict:
 v=gh(["issue","view",str(number),"--repo",repo,"--json","number,state,labels,comments"])
 labels=[x["name"] for x in v.get("labels",[])]
 comments=v.get("comments",[])
 body="\n".join(str(x.get("body","")) for x in comments[-4:]).lower()
 halt=None
 if "usage_limit_exceeded" in body or "usage quota" in body: halt="usage_limit_exceeded"
 elif "continuation-budget-stop" in body or "continuation policy" in body: halt="continuation_policy"
 elif "resource" in body and "unavailable" in body: halt="resource_unavailable"
 elif "preflight" in body and ("recoverable" in body or "cleared" in body): halt="preflight_recoverable"
 elif any(x.lower()=="symphony:halted" for x in labels): halt="unknown_halt"
 return {"labels":labels,"closed":v.get("state")=="CLOSED","halt_kind":halt,
         "manual_action_required":"manual_action_required" in body or "manual action required: yes" in body}

def quota_state()->dict:
 try:
  subprocess.run([sys.executable,str(HERE/"codex-usage-snapshot.py"),"--write","--quiet"],timeout=30,check=False)
 except (OSError,subprocess.TimeoutExpired): pass
 q=read(STATE_ROOT/"usage"/"current.json",{})
 rate=q.get("rateLimits",{})
 return {"primary_remaining":(rate.get("primary") or {}).get("remainingPercent",0),
         "weekly_remaining":(rate.get("secondary") or {}).get("remainingPercent",0),
         "status":q.get("status","unavailable"),"observedAt":q.get("observedAt")}

def active_worker()->bool:
 d=STATE_ROOT/"workers"/"active"
 return d.exists() and any(d.glob("*.json"))

def snapshot(config:dict,repo:str)->dict:
 issues={}; completed=[]
 for item in config.get("work_plan",[]):
  n=int(item["issue"])
  try:s=issue_state(repo,n)
  except Exception as exc:s={"labels":[],"halt_kind":"state_unavailable","error":str(exc)}
  issues[str(n)]=s
  if s.get("closed"): completed.append(n)
 return {"active_worker":active_worker(),"active_issue":active_issue(),"completed":completed,"issues":issues,"quota":quota_state()}

def mutate(repo:str,decision:dict,state:dict)->str:
 n=int(decision["issue"]); labels={x.lower() for x in state["issues"][str(n)].get("labels",[])}
 if "symphony:human-review" in labels or "symphony:human-attention" in labels: return "human gate"
 if "symphony:ready" in labels: return "already ready"
 if "symphony:halted" in labels:
  subprocess.run([str(HERE/"rearm-issue.sh"),str(n)],check=True,timeout=60)
  return "rearmed"
 gh(["issue","edit",str(n),"--repo",repo,"--add-label","symphony:ready"])
 return "armed"

def tick(config:dict,repo:str)->dict:
 now=datetime.now(timezone.utc); previous=read(STATUS,{}); state=snapshot(config,repo)
 decision=plan.evaluate(config,state,now)
 result={"protocolVersion":1,"observedAt":now.isoformat(),"decision":decision,"state":state,"repo":repo,"window":config.get("autonomous_window",{}),"workPlan":config.get("work_plan",[]),"paused":bool(config.get("paused")),"stopAfterIssue":config.get("stop_after_issue")}
 stop_target=config.get("stop_after_issue")\n stop_life=state.get("issues",{}).get(str(stop_target),{}) if stop_target is not None else {}\n stop_labels={str(x).lower() for x in stop_life.get("labels",[])}\n stop_reached=stop_target is not None and (int(stop_target) in state.get("completed",[]) or bool(stop_labels & HUMAN) or stop_life.get("manual_action_required") or (stop_life.get("halt_kind") and stop_life.get("halt_kind") not in plan.RECOVERABLE))\n if config.get("paused") or stop_reached: decision={"state":"paused","dispatch":False,"reason":"operator pause/stop boundary","stopAfterIssue":stop_target}; result["decision"]=decision
 if decision.get("dispatch"):
  try: result["actionResult"]=mutate(repo,decision,state)
  except Exception as exc: result["decision"]={"state":"human_gate","dispatch":False,"issue":decision.get("issue")}; result["error"]=str(exc)
 atomic_write(STATUS,result); return result

def update_control(action:str)->dict:
 c=read(CONFIG,{})
 w=c.setdefault("autonomous_window",{})
 if action=="enable": w["enabled"]=True
 elif action=="disable": w["enabled"]=False
 elif action=="pause": c["paused"]=True
 elif action=="resume": c["paused"]=False
 else: raise ValueError(action)
 atomic_write(CONFIG,c); return c

def main()->int:
 p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd",required=True)
 run=sub.add_parser("run"); run.add_argument("--interval",type=int,default=60); run.add_argument("--repo",default=DEFAULT_REPO)
 once=sub.add_parser("tick"); once.add_argument("--repo",default=DEFAULT_REPO)
 cfg=sub.add_parser("configure"); cfg.add_argument("--from-file",required=True)
 ctl=sub.add_parser("control"); ctl.add_argument("action",choices=["enable","disable","pause","resume","stop-after-issue","plan-enable","plan-disable","move-up","move-down"]); ctl.add_argument("--issue",type=int)
 sub.add_parser("status")
 a=p.parse_args()
 if a.cmd=="configure":
  value=json.loads(Path(a.from_file).read_text()); atomic_write(CONFIG,value); print(json.dumps(value,indent=2)); return 0
 if a.cmd=="control": print(json.dumps(update_control(a.action,a.issue),indent=2)); return 0
 if a.cmd=="status": print(json.dumps(read(STATUS,{"state":"not_started"}),indent=2)); return 0
 if not CONFIG.exists():
  print("Autonomous scheduler disabled: configure a durable plan first.",file=sys.stderr); return 0
 if a.cmd=="tick": print(json.dumps(tick(read(CONFIG,{}),a.repo),indent=2)); return 0
 while True:
  try: tick(read(CONFIG,{}),a.repo)
  except Exception as exc: atomic_write(STATUS,{"protocolVersion":1,"observedAt":datetime.now(timezone.utc).isoformat(),"decision":{"state":"human_gate","dispatch":False},"error":str(exc)})
  time.sleep(max(15,a.interval))
if __name__=="__main__": raise SystemExit(main())
