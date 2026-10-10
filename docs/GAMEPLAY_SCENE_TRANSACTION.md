# Gameplay scene transaction: phase 1 authorization policy

Issue: #198. This branch adds a **fail-closed request/receipt policy** for a
future, explicit `existing-scene-gameplay` tier. It does **not** enable that
tier or authorize any production scene editing.

## What the policy enforces

- An authenticated `GH-N` issue, exact workspace and `codex/` branch must
  agree with a host-owned grant. Workers must not write their own grants.
- Exact Unity scene asset path, nonempty authorized hierarchy roots, and a
  finite reviewed operation set.
- Only one `compose-opening-encounter` transaction operation is accepted.
  Requests carrying arbitrary script/method/command execution, broad source
  scenes, or a self-declared hierarchy exception are rejected.
- Validation is a separate gate from gameplay correctness, Unity validation,
  source diff review and human merge approval.

## Mandatory remaining integration before the tier can be used

1. Populate the new grant from host-owned issue approval and dispatch state,
   validating the original approval, allowed roots, and operation in the
   project-owned capability manifest. Do not accept worktree grants.
2. Invoke the policy at client, broker and host boundaries. The Unity Windows
   runner and project executor must independently enforce the operation and
   protected-root bounds (including clone/reference-owner exceptions).
3. Confirm that both the staged Unity process and its output publication
   cannot write outside approved assets. **A staged project mirror alone does
   not sandbox Editor C# or restrict host filesystem/network access.** Until
   that is resolved, allow only reviewed typed Editor entrypoints and never
   an agent-provided C# command.
4. Add negative end-to-end tests for wrong receipt, object scope, unauthorized
   output, protected-object changes, and rollback.
5. Require fresh Unity EditMode/PlayMode validation and human approval for
   RPG Kingdom #255/#250.

The new tier is intentionally **not** added to `unity-author.sh`,
`unity-author-host.sh`, `run-unity-authoring.ps1` or `WORKFLOW.md` yet.
Any request for it therefore remains rejected by the existing runtime. This
prevents a partial implementation from silently granting edit authority.

## Unit test

```sh
python3 -m unittest discover -s tests -p test_gameplay_scene_transaction_policy.py -v
```
