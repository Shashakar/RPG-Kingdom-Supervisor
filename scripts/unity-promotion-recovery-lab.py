#!/usr/bin/env python3
"""C5d private scratch transaction lab: real disk fsync/crashes, NO game copyback.

The CLI creates its own temporary fixtures. It never accepts a source, stage,
destination, or approval path. This is a recovery test harness, not an asset
promotion implementation or a security boundary for arbitrary Python callers.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile

LAB_MARKER = "RPGK-C5D-SCRATCH-LAB-V1"
FILES = (
    "Assets/RPGKingdom/Generated/AgentBenchmarks.meta",
    "Assets/RPGKingdom/Generated/AgentBenchmarks/RealWorkerStageProof.unity",
    "Assets/RPGKingdom/Generated/AgentBenchmarks/RealWorkerStageProof.unity.meta",
)
CHECKPOINTS = (
    "after-prepare", "after-reserve", "after-first-copy",
    "after-all-copy", "after-validation", "after-commit",
)
NONCE = re.compile(r"[0-9a-f]{32,64}\Z")


class LabDenied(Exception):
    pass


def _fsync_dir(folder: Path) -> None:
    descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_bytes(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise LabDenied("linked scratch output")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + secrets.token_hex(8))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def _atomic_json(path: Path, data: object) -> None:
    _atomic_bytes(path, (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode())


def _read_json(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise LabDenied("missing or linked scratch journal")
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _root(root: Path) -> Path:
    # Protect the CLI's scratch-only contract from accidental real-project paths.
    if root.is_symlink():
        raise LabDenied("linked scratch root")
    resolved = root.resolve(strict=True)
    if resolved.parent != Path(tempfile.gettempdir()).resolve():
        raise LabDenied("scratch root must be an immediate OS-temp child")
    if resolved.stat().st_mode & 0o077:
        raise LabDenied("scratch root must be owner-private")
    marker = resolved / ".rpgk-c5d-scratch"
    if marker.is_symlink() or not marker.is_file() or marker.read_text() != LAB_MARKER:
        raise LabDenied("scratch marker missing")
    return resolved


def _target(root: Path, subfolder: str, rel: str) -> Path:
    if rel not in FILES or subfolder not in ("stage", "destination"):
        raise LabDenied("path not part of fixed scratch fixture")
    candidate = root / subfolder
    for piece in rel.split("/"):
        candidate = candidate / piece
        if candidate.is_symlink():
            raise LabDenied("linked scratch path")
    return candidate


@contextmanager
def _exclusive(root: Path):
    checked = _root(root)
    lock_path = checked / "state" / "txn.lock"
    if lock_path.is_symlink():
        raise LabDenied("linked scratch lock")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LabDenied("scratch transaction locked by another process") from exc
        yield checked
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def create_fixture(root: Path) -> None:
    if root.is_symlink() or not root.is_dir() or root.resolve().parent != Path(tempfile.gettempdir()).resolve():
        raise LabDenied("fixture location must be a fresh OS-temp child")
    root.chmod(0o700)
    (root / ".rpgk-c5d-scratch").write_text(LAB_MARKER, encoding="utf-8")
    (root / "state").mkdir(mode=0o700)
    (root / "stage").mkdir(mode=0o700)
    (root / "destination").mkdir(mode=0o700)
    payloads = (
        b"fileFormatVersion: 2\nguid: " + b"a"*32 + b"\n",
        b"--- !u!1 &1\nGameObject:\n  m_Name: SupervisorRealWorkerRoot\n",
        b"fileFormatVersion: 2\nguid: " + b"b"*32 + b"\n",
    )
    for rel, data in zip(FILES, payloads):
        _atomic_bytes(_target(_root(root), "stage", rel), data)
    _atomic_json(root / "state" / "ledger.json", {"schemaVersion": 1, "reserved": []})
    _fsync_dir(root / "state")


def _stage_hashes(root: Path) -> dict[str, str]:
    result = {}
    for rel in FILES:
        src = _target(root, "stage", rel)
        if not src.is_file():
            raise LabDenied("missing fixture stage asset")
        result[rel] = _hash(src)
    return result


def _checkpoint(point: str, injected: str | None) -> None:
    if injected == point:
        # Simulate hard process death without cleanup handlers; parent recovers.
        os._exit(83)


def _validate_journal(journal: object) -> dict:
    if not isinstance(journal, dict) or set(journal) != {
        "schemaVersion", "nonce", "phase", "stageHashes", "applied"
    }:
        raise LabDenied("invalid scratch journal shape")
    if journal["schemaVersion"] != 1 or not isinstance(journal["nonce"], str) or not NONCE.fullmatch(journal["nonce"]):
        raise LabDenied("invalid scratch journal identity")
    hashes = journal["stageHashes"]
    if not isinstance(hashes, dict) or set(hashes) != set(FILES) or any(
        not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes.values()
    ):
        raise LabDenied("invalid pinned stage hashes")
    if not isinstance(journal["applied"], list) or not set(journal["applied"]).issubset(FILES):
        raise LabDenied("invalid applied asset list")
    if journal["phase"] not in ("prepared", "reserved", "copying", "copied",
                                "validated", "committed", "rolled-back"):
        raise LabDenied("invalid scratch phase")
    return journal


def transact(root: Path, nonce: str, crash_at: str | None = None) -> None:
    if not NONCE.fullmatch(nonce) or crash_at not in (None, *CHECKPOINTS):
        raise LabDenied("invalid nonce or crash injection")
    with _exclusive(root) as root:
        ledger_path = root / "state" / "ledger.json"
        journal_path = root / "state" / "journal.json"
        ledger = _read_json(ledger_path)
        if not isinstance(ledger, dict) or set(ledger) != {"schemaVersion", "reserved"}:
            raise LabDenied("invalid ledger")
        if ledger["schemaVersion"] != 1 or not isinstance(ledger["reserved"], list):
            raise LabDenied("invalid ledger version")
        if nonce in ledger["reserved"]:
            raise LabDenied("nonce already reserved")
        if journal_path.exists():
            prior = _validate_journal(_read_json(journal_path))
            if prior["phase"] not in ("committed", "rolled-back"):
                raise LabDenied("unrecovered previous scratch transaction")
            raise LabDenied("single-transaction scratch fixture is already used")
        if any(_target(root, "destination", rel).exists() for rel in FILES):
            raise LabDenied("scratch destination must start empty")
        plan = _stage_hashes(root)
        journal = {
            "schemaVersion": 1, "nonce": nonce, "phase": "prepared",
            "stageHashes": plan, "applied": [],
        }
        _atomic_json(journal_path, journal)
        _checkpoint("after-prepare", crash_at)
        ledger["reserved"].append(nonce)
        _atomic_json(ledger_path, ledger)
        journal["phase"] = "reserved"
        _atomic_json(journal_path, journal)
        _checkpoint("after-reserve", crash_at)
        for index, rel in enumerate(FILES):
            source = _target(root, "stage", rel)
            if _hash(source) != plan[rel]:
                raise LabDenied("scratch stage mutated after reservation")
            _atomic_bytes(_target(root, "destination", rel), source.read_bytes())
            journal["applied"].append(rel)
            journal["phase"] = "copying"
            _atomic_json(journal_path, journal)
            if index == 0:
                _checkpoint("after-first-copy", crash_at)
        journal["phase"] = "copied"
        _atomic_json(journal_path, journal)
        _checkpoint("after-all-copy", crash_at)
        if any(_hash(_target(root, "destination", rel)) != plan[rel] for rel in FILES):
            raise LabDenied("scratch copied fixture validation failed")
        journal["phase"] = "validated"
        _atomic_json(journal_path, journal)
        _checkpoint("after-validation", crash_at)
        journal["phase"] = "committed"
        _atomic_json(journal_path, journal)
        _checkpoint("after-commit", crash_at)


def recover(root: Path) -> dict:
    with _exclusive(root) as root:
        journal_path = root / "state" / "journal.json"
        if not journal_path.exists():
            return {"status": "nothing-to-recover", "nonceReserved": False}
        journal = _validate_journal(_read_json(journal_path))
        ledger = _read_json(root / "state" / "ledger.json")
        if not isinstance(ledger, dict) or not isinstance(ledger.get("reserved"), list):
            raise LabDenied("invalid nonce ledger during recovery")
        reserved = journal["nonce"] in ledger["reserved"]
        if journal["phase"] not in ("prepared", "rolled-back") and not reserved:
            raise LabDenied("reserved nonce missing from durable ledger")
        # Never silently remove a foreign modification or unexpected destination file.
        dest = root / "destination"
        observed = set()
        for walk_root, dirs, files in os.walk(dest, followlinks=False):
            for folder in dirs:
                if (Path(walk_root) / folder).is_symlink():
                    raise LabDenied("foreign linked directory in destination")
            for file in files:
                full = Path(walk_root) / file
                if full.is_symlink() or not full.is_file():
                    raise LabDenied("foreign linked or nonregular destination file")
                observed.add(full.relative_to(dest).as_posix())
        if not observed.issubset(FILES):
            raise LabDenied("foreign destination file detected; refuse recovery")
        for rel in observed:
            if _hash(_target(root, "destination", rel)) != journal["stageHashes"][rel]:
                raise LabDenied("destination modified by another actor; manual recovery required")
        if journal["phase"] == "committed":
            if observed != set(FILES):
                raise LabDenied("committed journal has missing destination asset")
            return {"status": "committed-preserved", "nonceReserved": reserved,
                    "filesRemaining": len(observed)}
        # Idempotent rollback of only the exact known fixture outputs.
        for rel in sorted(observed):
            path = _target(root, "destination", rel)
            path.unlink()
            _fsync_dir(path.parent)
        journal["phase"] = "rolled-back"
        _atomic_json(journal_path, journal)
        return {"status": "rolled-back", "nonceReserved": reserved, "filesRemaining": 0}


def _child(root: Path, nonce: str, crash_at: str | None) -> None:
    transact(root, nonce, crash_at)


def run_scenario(checkpoint: str | None = None) -> dict:
    if checkpoint not in (None, *CHECKPOINTS):
        raise LabDenied("unknown checkpoint")
    with tempfile.TemporaryDirectory(prefix="rpgk-c5d-scratch-") as tmp:
        root = Path(tmp)
        create_fixture(root)
        # Child is a distinct process: os._exit() bypasses Python cleanup entirely.
        worker = multiprocessing.get_context("fork").Process(
            target=_child, args=(root, "f"*48, checkpoint))
        worker.start()
        worker.join(timeout=15)
        if worker.is_alive():
            worker.kill()
            worker.join()
            raise LabDenied("scratch process hung")
        expected = 0 if checkpoint is None else 83
        if worker.exitcode != expected:
            raise LabDenied(f"scratch child exited {worker.exitcode}, expected {expected}")
        report = recover(root)
        repeat = recover(root)
        if report != repeat:
            raise LabDenied("recovery must be idempotent")
        replay = False
        try:
            transact(root, "f"*48)
        except LabDenied:
            replay = True
        if not replay:
            raise LabDenied("replayed transaction unexpectedly accepted")
        return {
            "scenario": checkpoint or "clean-commit",
            "childExit": worker.exitcode,
            "recovery": report,
            "nonceReplayRejected": replay,
            "sourceWorkspaceTouched": False,
            "realUnityInvocations": 0,
            "realGameFilesWritten": 0,
            "scratchOnly": True,
        }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inject-crash", choices=CHECKPOINTS)
    args = ap.parse_args()
    try:
        print(json.dumps(run_scenario(args.inject_crash), sort_keys=True))
        return 0
    except (LabDenied, OSError, ValueError) as exc:
        print(f"C5d scratch recovery lab blocked: {exc}", file=sys.stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
