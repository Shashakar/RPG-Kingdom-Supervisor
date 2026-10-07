#!/usr/bin/env python3
"""Record a human rejection of an RPG Kingdom PR as durable rework state."""
from __future__ import annotations
import argparse, json, os
from datetime import datetime, timezone
from urllib import error, parse, request

REPO=os.environ.get("RPGK_REPO","Shashakar/RPG-Kingdom")
API=os.environ.get("RPGK_GITHUB_API_ROOT","https://api.github.com").rstrip("/")
TOKEN=os.environ.get("SYMPHONY_GITHUB_TOKEN","")
STATE_MARKER="<!-- rpgk-review-state\n"
REWORK_MARKER="<!-- rpgk-human-rework\n"

def api(method,path,body=None):
    if not TOKEN: raise RuntimeError("SYMPHONY_GITHUB_TOKEN is required")
    data=None if body is None else json.dumps(body).encode()
    req=request.Request(f"{API}/repos/{REPO}{path}",data=data,method=method,headers={"Authorization":f"Bearer {TOKEN}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","Content-Type":"application/json"})
    try:
        with request.urlopen(req,timeout=30) as r:
            raw=r.read(); return json.loads(raw) if raw else None
    except error.HTTPError as exc:
        if method=="DELETE" and exc.code==404:return None
        raise RuntimeError(f"GitHub {method} {path} failed: HTTP {exc.code}") from exc

def parse_state(body):
    a=body.find(STATE_MARKER)
    if a<0:return None
    b=body.find("\n-->",a)
    if b<0:return None
    try:v=json.loads(body[a+len(STATE_MARKER):b])
    except json.JSONDecodeError:return None
    return v if isinstance(v,dict) else None

def latest_state(issue):
    comments=api("GET",f"/issues/{issue}/comments?per_page=100") or []
    found=None
    for c in comments:
        value=parse_state(str(c.get("body") or ""))
        if value is not None: found=value
    return found

def main():
    p=argparse.ArgumentParser()
    p.add_argument("issue",type=int); p.add_argument("pr",type=int)
    p.add_argument("--directive",required=True)
    a=p.parse_args()
    issue=api("GET",f"/issues/{a.issue}"); pr=api("GET",f"/pulls/{a.pr}")
    prior=latest_state(a.issue)
    if not prior or prior.get("state") not in {"human_review","human_attention"}:
        raise RuntimeError("human rework may only supersede durable human_review/human_attention state")
    if int(prior.get("prNumber",0))!=a.pr: raise RuntimeError("durable state points to a different PR")
    head=str(pr.get("head",{}).get("sha") or "")
    if not head: raise RuntimeError("PR head is unavailable")
    contract={"issue":a.issue,"prNumber":a.pr,"rejectedHead":head,"directive":a.directive.strip(),"recordedAt":datetime.now(timezone.utc).isoformat()}
    body=("## Human review — changes required\n\n"
          f"Head \`{head}\` is rejected for human rework. The following PR changes are required before integration:\n\n"
          f"{a.directive.strip()}\n\n{REWORK_MARKER}{json.dumps(contract,sort_keys=True,separators=(',',':'))}\n-->")
    comment=api("POST",f"/issues/{a.pr}/comments",{"body":body})
    state=dict(prior)
    state.update({"state":"human_rework","prNumber":a.pr,"prHeadSha":head,"lastVerdict":"human_changes_required","reason":"human_rework","humanRework":{"baselineHead":head,"currentHead":head,"directives":[a.directive.strip()],"prCommentId":comment.get("id") if isinstance(comment,dict) else None}})
    state["updatedAt"]=datetime.now(timezone.utc).isoformat()
    encoded=json.dumps(state,sort_keys=True,separators=(",",":"))
    api("POST",f"/issues/{a.issue}/comments",{"body":f"Human review rejected PR #{a.pr} at \`{head}\`. Authoritative rework details are recorded on the PR.\n\n{STATE_MARKER}{encoded}\n-->"})
    for label in ("symphony:human-review","symphony:human-attention","symphony:agent-review","symphony:halted"):
        api("DELETE",f"/issues/{a.issue}/labels/{parse.quote(label,safe='')}")
    api("POST",f"/issues/{a.issue}/labels",{"labels":["symphony:rework"]})
    print(json.dumps({"issue":a.issue,"pr":a.pr,"rejectedHead":head,"state":"human_rework","prCommentId":contract.get("prCommentId")}))

if __name__=="__main__": main()
