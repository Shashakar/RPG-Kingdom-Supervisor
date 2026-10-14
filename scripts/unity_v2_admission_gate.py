"""Keeper-only gate release validation. Never trust a worker-provided receipt."""
import os

def validate_registration(reply, pid, run_id):
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise PermissionError("registration rejected")
    record = reply.get("registration")
    if not isinstance(record, dict):
        raise PermissionError("missing registration")
    if record.get("pid") != pid or record.get("runId") != run_id:
        raise PermissionError("identity mismatch")
    ticks = record.get("processStartTicks")
    if not isinstance(ticks, str) or not ticks.isdecimal():
        raise PermissionError("bad process start time")
    return ticks

def admit(write_fd, reply, pid, run_id):
    ticks = validate_registration(reply, pid, run_id)
    if os.write(write_fd, bytes([71])) != 1:
        raise OSError("admission gate release failed")
    return ticks
