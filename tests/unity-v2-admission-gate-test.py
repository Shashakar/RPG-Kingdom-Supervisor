#!/usr/bin/env python3
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from unity_v2_admission_gate import admit, validate_registration

good = {"ok":True, "registration":{"pid":82,"runId":"run-82",
                                     "processStartTicks":"9021"}}
for request in [{}, {"ok":False}, {"ok":True},
                {"ok":True,"registration":{**good["registration"],"pid":83}},
                {"ok":True,"registration":{**good["registration"],"runId":"wrong"}},
                {"ok":True,"registration":{**good["registration"],"processStartTicks":"not a number"}}]:
    read_fd,write_fd=os.pipe()
    try:
        try:
            admit(write_fd,request,82,"run-82")
        except PermissionError:
            pass
        else:
            raise AssertionError("unapproved admission")
        os.close(write_fd)
        write_fd=-1
        assert os.read(read_fd,1)==b""
    finally:
        os.close(read_fd)
        if write_fd>=0:
            os.close(write_fd)

read_fd,write_fd=os.pipe()
try:
    assert admit(write_fd,good,82,"run-82")=="9021"
    assert os.read(read_fd,1)==bytes([71])
finally:
    os.close(read_fd)
    os.close(write_fd)
print("unity-v2-admission-gate-test: PASS")
