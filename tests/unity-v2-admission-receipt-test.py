#!/usr/bin/env python3
"""Host receipt authenticity, scope and expiry; never issues through a worker."""
import copy
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from unity_admission_receipt import issue_receipt, verify_receipt, validate_submitted_receipt
import tempfile

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
with tempfile.TemporaryDirectory() as temporary:
    key_path = Path(temporary) / "host.key"
    key_path.write_bytes(secret)
    check = dict(key_path=str(key_path), issue=scope["issue"], workspace=scope["workspace"],
                 worker_run_id=scope["worker_run_id"], operation=scope["operation"],
                 request_id=scope["request_id"])
    # Legacy compatibility remains deliberate until trusted submission is installed.
    assert validate_submitted_receipt({}, **check)
    assert validate_submitted_receipt({"admissionReceipt": receipt}, **check) is False  # expired at real clock
    live_receipt = issue_receipt(secret=secret, **scope)
    assert validate_submitted_receipt({"admissionReceipt": live_receipt}, **check)
    assert not validate_submitted_receipt({"admissionReceipt": live_receipt}, **{**check, "operation":"editmode"})
    assert not validate_submitted_receipt({"admissionReceipt": live_receipt}, **{**check, "key_path":None})
    bad = copy.deepcopy(live_receipt)
    bad["payload"]["issue"] = "GH-322"
    assert not validate_submitted_receipt({"admissionReceipt": bad}, **check)
print("unity-v2-admission-receipt-test: PASS")
