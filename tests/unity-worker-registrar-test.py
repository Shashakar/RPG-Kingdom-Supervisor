#!/usr/bin/env python3
"""Only the distinct, authorized controller UID can register workers."""
import importlib.util
import os
from pathlib import Path
import sys
from unittest.mock import patch

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/"scripts"))
spec=importlib.util.spec_from_file_location("registrar",root/"scripts"/"unity-worker-registrar.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
controller=os.getuid()+100
worker=os.getuid()+200
record={"action":"registerChild","issue":"GH-321","workspace":"/tmp/GH-321",
        "pid":4321,"runId":"GH-321-test"}
kwargs={"controller_uid":controller,"worker_uid":worker,
        "state_root":Path("/unused"),"workspace_root":Path("/unused")}
for peer in (os.getuid(),worker,controller+1):
    try:
        module.dispatch(record,peer_uid=peer,**kwargs)
    except PermissionError:
        pass
    else:
        raise AssertionError("untrusted identity registered")
with patch.object(module,"verify_controller_sibling",return_value={"pid":4321}) as identity, \
     patch.object(module,"register",return_value={"pid":4321}) as registered:
    result=module.dispatch(record,peer_uid=controller,peer_pid=1111,**kwargs)
    assert result["ok"] and result["registration"]["pid"]==4321
    assert registered.call_args.kwargs["worker_uid"]==worker
    assert identity.call_args.kwargs["client_pid"]==1111
with patch.object(module,"revoke",return_value=True):
    result=module.dispatch({"action":"revoke","pid":4321,"runId":"GH-321-test",
                            "processStartTicks":"123"},peer_uid=controller,**kwargs)
    assert result=={"ok":True,"revoked":True}
for record in ({"action":"register","issue":"GH-321","pid":"4321","workspace":"/tmp/GH-321","runId":"GH-321-test"},
               {"action":"register","issue":"GH-321","pid":4321,"workspace":"/tmp/GH-321","runId":"GH-321-test","uid":worker},
               {"action":"unknown"}):
    try:
        module.dispatch(record,peer_uid=controller,**kwargs)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid registry request accepted")
# The old unauthenticated admission shape is never permitted.
try:
    module.dispatch({**record, "action":"register", "issue":"GH-321",
                     "workspace":"/tmp/GH-321", "pid":4321, "runId":"GH-321-test"},
                    peer_uid=controller,peer_pid=1111,**kwargs)
except ValueError:
    pass
else:
    raise AssertionError("legacy registration bypass remains")
print("unity-worker-registrar-test: PASS")
