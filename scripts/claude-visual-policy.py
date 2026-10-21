#!/usr/bin/env python3
"""Fail-closed Rekindle visual-experiment contract for autonomous Claude.

This is an explicitly opt-in, data-only host lane. Claude writes a bounded JSON
manifest; it never receives Bash, arbitrary Unity Editor method invocation, or
permission to commit locally licensed source assets.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

LABELS = frozenset({
    "agent:claude", "work:visual-unity", "resource:unity-editor",
    "validation:unity-required", "authoring:scene-new-composition",
})
AUTHORITY_MARKER = "<!-- rpgk-claude-visual-authority\n"
ISSUE_RE = re.compile(r"GH-([1-9][0-9]*)\Z")
IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,47}\Z")
ASSET_RE = re.compile(r"Assets/(?:[A-Za-z0-9 _.-]+/)*[A-Za-z0-9 _.-]+\.(?:prefab|fbx|anim)\Z")
SUPPORTED_KINDS = ("character-comparison", "environment-cohesion")
MAX_OBJECTS = 18
MAX_SHOTS = 6
MAX_EXPERIMENTS = 2


class VisualContractError(ValueError):
    pass


def authority(body: str, issue_number: int) -> dict:
    """Require an exact, explicit issue-scoped human-authored authorization."""
    if body.count(AUTHORITY_MARKER) != 1:
        raise VisualContractError("one rpgk-claude-visual-authority block is required")
    text = body.split(AUTHORITY_MARKER, 1)[1]
    if "-->" not in text:
        raise VisualContractError("unterminated visual authority block")
    try:
        value = json.loads(text.split("-->", 1)[0])
    except (ValueError, TypeError) as exc:
        raise VisualContractError("visual authority must contain valid JSON") from exc
    if not isinstance(value, dict) or set(value) != {
        "protocolVersion", "issue", "phases", "allowLicensedScreenshots",
    }:
        raise VisualContractError("visual authority has missing or unexpected fields")
    if value["protocolVersion"] != 1 or value["issue"] != issue_number:
        raise VisualContractError("visual authority issue/protocol mismatch")
    phases = value["phases"]
    if not isinstance(phases, list) or not phases or len(phases) > MAX_EXPERIMENTS:
        raise VisualContractError("invalid visual experiment phases")
    if any(not isinstance(x, str) or x not in SUPPORTED_KINDS for x in phases):
        raise VisualContractError("unsupported visual phase")
    if phases != list(dict.fromkeys(phases)):
        raise VisualContractError("duplicate visual phases")
    if len(phases) == 2 and phases != list(SUPPORTED_KINDS):
        raise VisualContractError("character comparison must precede environment")
    if value["allowLicensedScreenshots"] is not True:
        raise VisualContractError("human screenshot/AI review approval is required")
    return value


def preflight_labels(labels: set[str], body: str, number: int) -> dict:
    labels = {x.lower() for x in labels}
    if not LABELS.issubset(labels):
        raise VisualContractError("Claude visual work requires agent, visual, Unity, validation and new-scene labels")
    if any(x.startswith("authoring:") and x != "authoring:scene-new-composition" for x in labels):
        raise VisualContractError("conflicting scene authoring tier")
    if any(x in labels for x in ("agent:codex", "agent:auto", "symphony:ready",
                                 "validation:unity-optional", "completion:report-only")):
        raise VisualContractError("conflicting provider, queue or validation labels")
    return authority(body, number)


def require_host_opt_in() -> None:
    if os.environ.get("RPGK_CLAUDE_VISUAL_UNITY_ENABLED") != "1":
        raise VisualContractError("visual Unity automation requires separate operator opt-in")
    if os.environ.get("RPGK_REKINDLE_UNITY_READY") != "1":
        raise VisualContractError("Rekindle Unity integration has not passed host validation")
    if os.environ.get("RPGK_CLAUDE_LICENSED_VISUAL_REVIEW_APPROVED") != "1":
        raise VisualContractError("licensed screenshot review has no host-side approval")


def manifest_path(workspace: Path, number: int) -> Path:
    if ISSUE_RE.fullmatch(workspace.name) is None or workspace.name != f"GH-{number}":
        raise VisualContractError("manifest workspace/issue mismatch")
    return workspace / "Docs" / "VisualExperiments" / workspace.name / "request.json"


def evidence_root(workspace: Path, number: int) -> Path:
    return workspace / "Docs" / "Evidence" / "VisualExperiments" / f"GH-{number}"


def _array3(value: object, field: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise VisualContractError(f"{field} must be a three-component vector")
    try:
        import math
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or
               not math.isfinite(x) or abs(x) > 1000 for x in value):
            raise VisualContractError(f"{field} contains invalid coordinates")
    except (TypeError, ValueError) as exc:
        raise VisualContractError(f"{field} contains invalid coordinates") from exc
    return [float(x) for x in value]


def _asset(value: object, suffixes: tuple[str, ...]) -> str:
    if not isinstance(value, str) or not ASSET_RE.fullmatch(value) or not value.endswith(suffixes):
        raise VisualContractError(f"invalid Unity asset path: {value!r}")
    if any(part in (".", "..") for part in value.split("/")):
        raise VisualContractError("unsafe Unity asset path")
    return value


def validate_manifest(value: object, auth: dict, number: int) -> dict:
    """Accept only data consumed by the fixed host-owned scene preview executor."""
    if not isinstance(value, dict) or set(value) != {"protocolVersion", "issue", "experiments"}:
        raise VisualContractError("unexpected visual manifest envelope")
    if value["protocolVersion"] != 1 or value["issue"] != number:
        raise VisualContractError("manifest issue/protocol mismatch")
    experiments = value["experiments"]
    if not isinstance(experiments, list) or len(experiments) != len(auth["phases"]):
        raise VisualContractError("manifest phases do not match approved issue authority")
    normalized = []
    for requested_kind, entry in zip(auth["phases"], experiments):
        if not isinstance(entry, dict) or set(entry) != {"kind", "objects", "shots", "camera"}:
            raise VisualContractError("unexpected experiment fields")
        if entry["kind"] != requested_kind:
            raise VisualContractError("experiment order/type differs from authorization")
        objects = entry["objects"]
        if not isinstance(objects, list) or not (2 <= len(objects) <= MAX_OBJECTS):
            raise VisualContractError("experiment requires 2-18 objects")
        seen = set()
        roles = set()
        sanitized = []
        for obj in objects:
            if not isinstance(obj, dict) or set(obj) != {"id", "role", "asset", "position", "yaw", "scale"}:
                raise VisualContractError("unexpected scene object fields")
            name = obj["id"]
            if not isinstance(name, str) or not IDENTIFIER_RE.fullmatch(name) or name in seen:
                raise VisualContractError("invalid or duplicate object id")
            seen.add(name)
            role = obj["role"]
            if role not in ("character", "building", "nature", "prop"):
                raise VisualContractError("invalid visual object role")
            roles.add(role)
            path = _asset(obj["asset"], (".prefab", ".fbx"))
            pos = _array3(obj["position"], "object position")
            yaw, scale = obj["yaw"], obj["scale"]
            import math
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                   for v in (yaw, scale)) or abs(yaw) > 360 or not (0.2 <= scale <= 5):
                raise VisualContractError("object yaw/scale outside bounded range")
            sanitized.append({"id": name, "role": role, "asset": path, "position": pos,
                              "yaw": float(yaw), "scale": float(scale)})
        if requested_kind == "character-comparison" and sum(o["role"] == "character" for o in sanitized) != 2:
            raise VisualContractError("character comparison requires exactly two characters")
        if requested_kind == "environment-cohesion" and not {"character", "building", "nature"}.issubset(roles):
            raise VisualContractError("environment trial requires character, building and nature")
        shots = entry["shots"]
        if not isinstance(shots, list) or not (1 <= len(shots) <= MAX_SHOTS):
            raise VisualContractError("experiment requires 1-6 screenshots")
        shot_names = set()
        clean_shots = []
        for shot in shots:
            if not isinstance(shot, dict) or set(shot) != {"id", "clip", "clipName", "seconds"}:
                raise VisualContractError("unexpected shot fields")
            name = shot["id"]
            if not isinstance(name, str) or not IDENTIFIER_RE.fullmatch(name) or name in shot_names:
                raise VisualContractError("invalid/duplicate shot id")
            shot_names.add(name)
            clip = shot["clip"]
            if clip:
                _asset(clip, (".anim", ".fbx"))
            elif requested_kind == "character-comparison":
                raise VisualContractError("character comparison must specify the shared animation")
            if not isinstance(shot["clipName"], str) or len(shot["clipName"]) > 100:
                raise VisualContractError("invalid clip name")
            seconds = shot["seconds"]
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not (0 <= seconds <= 30):
                raise VisualContractError("invalid animation sample time")
            clean_shots.append({"id": name, "clip": clip, "clipName": shot["clipName"],
                                "seconds": float(seconds)})
        cam = entry["camera"]
        if not isinstance(cam, dict) or set(cam) != {"position", "lookAt", "fov"}:
            raise VisualContractError("camera requires position/lookAt/fov")
        fov = cam["fov"]
        if isinstance(fov, bool) or not isinstance(fov, (int, float)) or not (20 <= fov <= 85):
            raise VisualContractError("camera fov outside bounds")
        normalized.append({"kind": requested_kind, "objects": sanitized, "shots": clean_shots,
                           "camera": {"position": _array3(cam["position"], "camera position"),
                                      "lookAt": _array3(cam["lookAt"], "camera target"),
                                      "fov": float(fov)}})
    return {"protocolVersion": 1, "issue": number, "experiments": normalized}


def read_manifest(workspace: Path, auth: dict, number: int) -> dict:
    path = manifest_path(workspace, number)
    if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != workspace.parent):
        raise VisualContractError("manifest or parent is a symlink")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VisualContractError("missing/malformed visual request manifest") from exc
    return validate_manifest(data, auth, number)
