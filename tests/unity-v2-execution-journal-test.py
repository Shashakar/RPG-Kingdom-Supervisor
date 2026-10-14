#!/usr/bin/env python3
"""SQLite admission survives restarts and concurrent broker reservations."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from unity_execution_journal import reserve_execution

with tempfile.TemporaryDirectory() as temp:
    state=Path(temp)
    ws=state/"workspaces"/"GH-321"
    ws.mkdir(parents=True)
    request_id="GH-321-author-one"
    def reserve():
        return reserve_execution(state,ws,"author",request_id)
    with ThreadPoolExecutor(max_workers=8) as executor:
        values=list(executor.map(lambda _:reserve(),range(16)))
    assert values.count(True)==1,values
    assert values.count(False)==15,values
    # New process lifetimes continue to see the durable reservation.
    assert not reserve_execution(state,ws,"author",request_id)
    assert reserve_execution(state,ws,"editmode",request_id)
    assert reserve_execution(state,ws,"author","GH-321-author-two")
    for invalid_workspace,operation,identifier in [
        (ws,"health","GH-321-health"),
        (ws,"author","GH-322-author-one"),
        (ws,"author","bad"),
        (state/"workspaces"/"not-an-issue","author","GH-321-author-one"),
    ]:
        try:
            reserve_execution(state,invalid_workspace,operation,identifier)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid journal scope permitted")
    with sqlite3.connect(state/"unity-execution-journal.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]==3
print("unity-v2-execution-journal-test: PASS")
