#!/usr/bin/env python3
"""Real process-termination tests against auto-created scratch files ONLY."""
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "c5d_scratch_lab", Path(__file__).resolve().parents[1]
    / "scripts/unity-promotion-recovery-lab.py")
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


def crash_worker(root, point):
    lab.transact(root, "a"*48, point)


def contention_worker(root, results):
    try:
        lab.transact(root, "b"*48)
    except lab.LabDenied as exc:
        results.put(str(exc))
    else:
        results.put("unexpected success")


class ScratchRecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="rpgk-c5d-scratch-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        lab.create_fixture(self.root)
        self.outside = self.root.parent / (self.root.name + "-outside-sentinel")
        self.outside.write_text("untouched")
        self.addCleanup(self.outside.unlink)

    def die(self, point):
        worker = multiprocessing.get_context("fork").Process(
            target=crash_worker, args=(self.root, point))
        worker.start()
        worker.join(timeout=10)
        if worker.is_alive():
            worker.kill()
            worker.join()
            self.fail("scratch worker hung")
        self.assertEqual(worker.exitcode, 83)

    def test_all_checkpoint_crashes_recover_and_preserve_external_files(self):
        for point in lab.CHECKPOINTS:
            with self.subTest(checkpoint=point):
                # Each scenario uses a brand-new internal private scratch root.
                outcome = lab.run_scenario(point)
                self.assertEqual(outcome["childExit"], 83)
                self.assertTrue(outcome["scratchOnly"])
                self.assertTrue(outcome["nonceReplayRejected"])
                self.assertEqual(outcome["realGameFilesWritten"], 0)
                self.assertEqual(outcome["realUnityInvocations"], 0)
                expected = "committed-preserved" if point == "after-commit" else "rolled-back"
                self.assertEqual(outcome["recovery"]["status"], expected)
        self.assertEqual(self.outside.read_text(), "untouched")

    def test_success_is_durable_and_replayed_nonce_denied(self):
        outcome = lab.run_scenario()
        self.assertEqual(outcome["childExit"], 0)
        self.assertEqual(outcome["recovery"]["status"], "committed-preserved")
        self.assertEqual(outcome["recovery"]["filesRemaining"], 3)
        self.assertTrue(outcome["recovery"]["nonceReserved"])
        self.assertTrue(outcome["nonceReplayRejected"])

    def test_resume_after_first_copy_rolls_back_exact_hashes_only(self):
        self.die("after-first-copy")
        report = lab.recover(self.root)
        self.assertEqual(report["status"], "rolled-back")
        self.assertTrue(report["nonceReserved"])
        self.assertEqual(lab.recover(self.root), report)
        for rel in lab.FILES:
            self.assertFalse((self.root / "destination" / rel).exists())
        self.assertEqual(self.outside.read_text(), "untouched")
        with self.assertRaisesRegex(lab.LabDenied, "nonce already reserved"):
            lab.transact(self.root, "a"*48)

    def test_crash_before_reservation_preserves_unspent_nonce(self):
        self.die("after-prepare")
        self.assertFalse(lab.recover(self.root)["nonceReserved"])
        self.assertEqual(json.loads((self.root / "state/ledger.json").read_text())["reserved"], [])

    def test_foreign_modified_destination_refuses_rollback(self):
        self.die("after-first-copy")
        first = self.root / "destination" / lab.FILES[0]
        first.write_text("a foreign modification")
        with self.assertRaisesRegex(lab.LabDenied, "manual recovery required"):
            lab.recover(self.root)
        self.assertEqual(first.read_text(), "a foreign modification")
        self.assertEqual(self.outside.read_text(), "untouched")

    def test_unexpected_file_and_symlink_refuse_recovery(self):
        self.die("after-reserve")
        foreign = self.root / "destination/Assets/Foreign.asset"
        foreign.parent.mkdir(parents=True)
        foreign.write_text("foreign")
        with self.assertRaisesRegex(lab.LabDenied, "foreign destination file"):
            lab.recover(self.root)
        foreign.unlink()
        linked = self.root / "destination/Assets/Link.asset"
        linked.symlink_to(self.outside)
        with self.assertRaisesRegex(lab.LabDenied, "foreign linked"):
            lab.recover(self.root)
        self.assertEqual(self.outside.read_text(), "untouched")

    def test_missing_reserved_nonce_blocks_recovery(self):
        self.die("after-first-copy")
        lab._atomic_json(self.root / "state/ledger.json",
                         {"schemaVersion": 1, "reserved": []})
        with self.assertRaisesRegex(lab.LabDenied, "reserved nonce missing"):
            lab.recover(self.root)

    def test_real_flock_contention_rejected_in_second_process(self):
        ctx = multiprocessing.get_context("fork")
        results = ctx.Queue()
        with lab._exclusive(self.root):
            other = ctx.Process(target=contention_worker, args=(self.root, results))
            other.start()
            other.join(timeout=10)
            if other.is_alive():
                other.kill()
                other.join()
                self.fail("second contender hung")
            self.assertEqual(other.exitcode, 0)
            self.assertIn("locked by another process", results.get(timeout=3))
        self.assertEqual(self.outside.read_text(), "untouched")

    def test_rejects_non_scratch_path_and_invalid_nonce(self):
        with self.assertRaisesRegex(lab.LabDenied, "scratch root must"):
            lab.recover(self.root / "destination")
        with self.assertRaisesRegex(lab.LabDenied, "invalid nonce"):
            lab.transact(self.root, "not-a-nonce")


if __name__ == "__main__":
    unittest.main()
