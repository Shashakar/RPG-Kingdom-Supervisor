#!/usr/bin/env python3
"""Host receipt authenticity, scope and expiry; never issues through a worker."""
import copy
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from unity_admission_receipt import issue_receipt, verify_receipt

secret=b"x"*32
scope=dict(issue="GH-321",workspace="/tmp/isolated/GH-321",
           worker_run_id="GH-321-implementation-run",operation="author",
           request_id="GH-321-author-job")
receipt=issue_receipt(secret=secret,**scope,now=1000)
assert verify_receipt(receipt,secret=secret,**scope,now=1001)
assert not verify_receipt(receipt,secret=secret,**scope,now=1120)
for key,value in [("issue","GH-322"),("workspace","/tmp/isolated/GH-322"),
                  ("worker_run_id","GH-321-implementation-other"),
                  ("operation","editmode"),("request_id","GH-321-author-other")]:
    changed={**scope,key:value}
    assert not verify_receipt(receipt,secret=secret,**changed,now=1001),key
assert not verify_receipt(receipt,secret=b"y"*32,**scope,now=1001)
for k,v in [("workerRunId","GH-321-implementation-other"),("operation","editmode"),
            ("expiresAt",99999)]:
    forgery=copy.deepcopy(receipt)
    forgery["payload"][k]=v
    assert not verify_receipt(forgery,secret=secret,**scope,now=1001),k
for invalid in [None,{"payload":{}, "mac":"a"*64},{"payload":receipt["payload"],"mac":"zz"*32}]:
    assert not verify_receipt(invalid,secret=secret,**scope,now=1001)
try: issue_receipt(secret=b"short",**scope,now=1000)
except ValueError: pass
else: raise AssertionError("short secret accepted")
try: issue_receipt(secret=secret,**scope,now=1000,ttl_seconds=301)
except ValueError: pass
else: raise AssertionError("overlong ttl accepted")
print("unity-v2-admission-receipt-test: PASS")
