#!/usr/bin/env python3
"""Regression: owned Unity work may outlive several summary-free model turns."""
import copy
import contextlib
import io
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('inflight_policy', root / 'scripts/continuation-policy.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def stamp(age=0):
    return (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()


class InflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.workspace = base / 'workspaces/GH-264'
        self.workspace.mkdir(parents=True)
        self.state = base / 'state'
        (self.state / 'unity-broker').mkdir(parents=True)
        self.worker = {'runId': 'GH-264-worker-current', 'pid': os.getpid()}
        self.quota = {'status': 'available', 'observedAt': stamp(), 'rateLimits': {
            'primary': {'remainingPercent': 80}, 'secondary': {'remainingPercent': 80}}}
        self.worker['quotaBefore'] = copy.deepcopy(self.quota)
        self.usage = {'status': 'available', 'inputTokens': 1000, 'cachedInputTokens': 900,
                      'outputTokens': 10, 'reasoningTokens': 0, 'totalTokens': 1010}
        self.snapshot = {'fingerprint': 'preserved-diff', 'dirty': True, 'head': 'a', 'gitStatus': ' M scene'}
        self.unity = {'runId': 'old-editmode-pass', 'result': 'Passed', 'failed': 0}
        self.previous = {'workspace': self.snapshot, 'unity': self.unity, 'quota': self.quota,
                         'usage': self.usage, 'progress': {'consecutiveInvisibleTurns': 5}}
        self.request = 'GH-264-inspect-current'
        self.status = {'protocolVersion': 1, 'state': 'running', 'updatedAt': stamp(),
                       'stallThresholdSeconds': 300, 'activeRequest': {
            'issue': 'GH-264', 'workspace': str(self.workspace), 'operation': 'inspect',
            'requestId': self.request, 'ownershipSource': 'worker-lease', 'workerAlive': True,
            'workerRunId': self.worker['runId'], 'workerPid': self.worker['pid'],
            'recoveryBlocked': False, 'progress': {'requestId': self.request, 'sequence': 11,
                'phase': 'capture_running', 'observedAt': stamp(4), 'editorLogBytes': 22735}}}
        policy.telemetry.state_root = lambda: self.state
        policy.telemetry.append_event = lambda *a, **kw: None
        policy.active_worker = lambda *a: self.worker
        policy.refresh_quota = lambda *a: self.quota
        policy.workspace_snapshot = lambda *a: self.snapshot
        policy.latest_unity = lambda *a: self.unity
        policy.usage_snapshot = lambda *a: self.usage
        policy.ensure_local_excludes = lambda *a: None
        os.environ['RPGK_CONTINUATION_FOLLOW_THROUGH'] = '1'

    def evaluate(self, turn=6, labels=None):
        (self.state / 'unity-broker/status.json').write_text(json.dumps(self.status))
        state_path, _ = policy.state_paths(self.workspace)
        policy.atomic_json(state_path, self.previous)
        with contextlib.redirect_stdout(io.StringIO()):
            result = policy.evaluate(self.workspace, 'GH-264', turn, 8,
                                     labels if labels is not None else ['resource:unity-editor'])
        return result, json.loads(state_path.read_text())

    def test_live_request_is_not_invisible_or_completed_validation(self):
        code, record = self.evaluate()
        self.assertEqual(code, 0)
        self.assertEqual(record['progress']['consecutiveInvisibleTurns'], 0)
        self.assertEqual(record['progress']['unityInFlight']['sequence'], 11)
        self.assertEqual(record['unity']['runId'], 'old-editmode-pass')
        self.assertFalse(record['progress']['unityChanged'])

    def test_running_request_does_not_need_a_new_sequence_each_model_turn(self):
        self.status['activeRequest']['progress']['observedAt'] = stamp(100)
        self.assertEqual(self.evaluate()[0], 0)

    def test_missing_or_wrong_ownership_and_stale_progress_do_not_grant_continuation(self):
        original = copy.deepcopy(self.status)
        mutations = [
            lambda s: s.update(protocolVersion=99),
            lambda s: s.update(state='ready'),
            lambda s: s.update(updatedAt=stamp(60)),
            lambda s: s['activeRequest'].update(workspace='/tmp/other'),
            lambda s: s['activeRequest'].update(issue='GH-260'),
            lambda s: s['activeRequest'].update(workerRunId='prior-worker'),
            lambda s: s['activeRequest'].update(workerPid=123456),
            lambda s: s['activeRequest'].update(workerAlive=False),
            lambda s: s['activeRequest'].update(recoveryBlocked=True),
            lambda s: s['activeRequest'].update(operation='health'),
            lambda s: s['activeRequest']['progress'].update(observedAt=stamp(301)),
            lambda s: s['activeRequest']['progress'].update(requestId='GH-260-other'),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.status = copy.deepcopy(original)
                mutate(self.status)
                self.assertNotEqual(self.evaluate()[0], 0)

    def test_terminal_response_wins_over_running_projection(self):
        terminal = self.workspace / 'Logs/SymphonyUnity/.broker/responses' / (self.request + '.json')
        terminal.parent.mkdir(parents=True)
        terminal.write_text('{"status":"ClientAbandoned","exitCode":93}')
        self.assertNotEqual(self.evaluate()[0], 0)

    def test_no_unity_label_does_not_grant_continuation(self):
        self.assertNotEqual(self.evaluate(labels=[])[0], 0)

    def test_report_only_semantics_are_unchanged(self):
        self.snapshot['dirty'] = False
        code, record = self.evaluate(labels=['resource:unity-editor', 'completion:report-only'])
        self.assertEqual(code, 0)
        self.assertNotIn('unityInFlight', record['progress'])

    def test_inflight_is_not_an_extra_analysis_grace_setting(self):
        os.environ['RPGK_CONTINUATION_FOLLOW_THROUGH'] = '0'
        self.assertEqual(self.evaluate()[0], 0)

    def test_hard_turn_and_quota_and_lifetime_budget_still_stop(self):
        self.assertNotEqual(self.evaluate(turn=8)[0], 0)
        self.quota['rateLimits']['primary']['remainingPercent'] = 5
        self.assertNotEqual(self.evaluate()[0], 0)
        self.quota['rateLimits']['primary']['remainingPercent'] = 45
        code, record = self.evaluate()
        self.assertNotEqual(code, 0)
        self.assertFalse(record['dynamicBudget']['productiveOverride'])

    def test_waiting_does_not_override_the_per_turn_spend_limit(self):
        self.previous['quota'] = copy.deepcopy(self.quota)
        self.worker['quotaBefore']['rateLimits']['primary']['remainingPercent'] = 90
        self.quota['rateLimits']['primary']['remainingPercent'] = 64
        code, record = self.evaluate()
        self.assertNotEqual(code, 0)
        self.assertEqual(record['dynamicBudget']['turnPrimarySpendPercent'], 16)
        self.assertFalse(record['dynamicBudget']['productiveOverride'])


if __name__ == '__main__':
    unittest.main()
