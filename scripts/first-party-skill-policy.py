#!/usr/bin/env python3
"""Deterministic, host-owned first-party skill selection for RPG Kingdom workers."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import supervisor_telemetry as telemetry  # type: ignore  # noqa: E402

SKILL_BY_RISK = {
    "risk:mechanical": "rpgk-mechanical-change",
    "risk:investigative": "rpgk-investigate-bug",
    "risk:architecture": "rpgk-architecture-change",
    "risk:end-to-end": "rpgk-end-to-end-change",
}


def parse_labels(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"labels JSON is invalid: {exc}") from exc
    if not isinstance(parsed, list):
        raise RuntimeError("labels JSON must be an array")
    return [str(item).strip().lower() for item in parsed if str(item).strip()]


def skills_root() -> Path:
    supervisor_root = Path(os.environ.get("RPGK_SUPERVISOR_ROOT", str(SCRIPT_DIR.parent))).expanduser().resolve()
    return Path(os.environ.get("RPGK_SUPERVISOR_SKILLS_ROOT", str(supervisor_root / "skills"))).expanduser().resolve()


def select(labels: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    normalized = {item.lower() for item in labels}
    selected_risks = [risk for risk in SKILL_BY_RISK if risk in normalized]
    if len(selected_risks) > 1:
        raise RuntimeError(f"conflicting first-party skill risk labels: {', '.join(selected_risks)}")

    selected = [SKILL_BY_RISK[selected_risks[0]]] if selected_risks else []
    root = skills_root()
    catalog: list[dict[str, Any]] = []
    for risk, name in SKILL_BY_RISK.items():
        path = root / name / "SKILL.md"
        is_selected = name in selected
        entry = {
            "name": name,
            "risk": risk,
            "selected": is_selected,
            "available": path.is_file(),
            "path": str(path),
            "reason": risk if is_selected else "risk class not selected",
        }
        if is_selected and not entry["available"]:
            raise RuntimeError(f"selected first-party skill is missing: {path}")
        catalog.append(entry)
    return selected, catalog


def augment(state_file: Path, labels: list[str]) -> dict[str, Any]:
    state = telemetry.read_json(state_file)
    if not state:
        raise RuntimeError(f"capability state is missing or invalid: {state_file}")
    selected, catalog = select(labels)
    state["selectedSkills"] = selected
    state["skillCatalog"] = catalog
    telemetry.atomic_json(state_file, state)
    telemetry.append_event(
        "worker_first_party_skills_selected",
        issue=state.get("issue"),
        route=state.get("route"),
        riskLabels=state.get("riskLabels"),
        selectedSkills=selected,
    )
    return telemetry.sanitize(state)


def main() -> int:
    parser = argparse.ArgumentParser(description="Select Supervisor-owned RPG Kingdom skills")
    parser.add_argument("--labels-json", default="[]")
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        result = augment(Path(args.state_file).expanduser().resolve(), parse_labels(args.labels_json))
        print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"RPG Kingdom first-party skill policy: {exc}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
