# Unity Scene Authoring

## Purpose

Supervisor provides a narrow host-owned seam for deterministic production-scene changes that RPG Kingdom explicitly classifies as an approved scene-authoring tier. The model chooses a typed request; Unity performs it through reviewed RPG Kingdom Editor code; Supervisor owns authorization, capability preflight, Windows/Unity execution, and copy-back into the GH workspace.

This does not grant general production-scene authority or creative scene composition.

## Tiers and authorization

Scene-authoring authority is dispatch-scoped, issue-scoped, workspace-scoped, and exact-tier matched.

| Issue label | Request tier | Scope |
| --- | --- | --- |
| `authoring:scene-mechanical` | `mechanical` | Tier 1: non-structural serialized mechanical configuration |
| `authoring:scene-structural` | `mechanical-structural` | Tier 2: separately reviewed, bounded structural operations implemented and allowlisted by RPG Kingdom, plus typed configuration |
| `authoring:scene-new-composition` | `new-scene-composition` | New-scene lane: create one issue-owned scene from an approved source, then iteratively compose only that unmerged target |

An authoring issue must also own `resource:unity-editor`. When validation is required it must carry the normal `validation:unity-required` label.

The three authoring labels are mutually exclusive. No authoring tier implicitly grants another tier. The Unity preflight writes the exact authorized request tier into a host-owned receipt for that worker lifetime. The client, broker, host adapter, and Windows staging runner reject unknown tiers; the broker and host additionally require the request tier to exactly match the receipt.

Scene-authoring authority is never inferred from issue prose, model intent, or possession of the Unity resource. Authorization and project executor capability are separate gates: the issue label grants one exact request tier, while the RPG Kingdom capability contract determines whether the declared operations/types are actually supported.

## Structural capability preflight

RPG Kingdom owns the reviewed capability surface in:

`Assets/RPGKingdom/Editor/SymphonyMechanicalSceneAuthoringCapabilities.json`

Supervisor consumes that checked-in contract from the issue workspace. It does **not** maintain its own copy of RPG Kingdom's structural allowlist.

A new Tier-2 issue should declare deterministic structural requirements in exactly one hidden JSON block:

```markdown
<!-- symphony-scene-authoring-requirements
{
  "mode": "known",
  "tier": "mechanical-structural",
  "operations": ["add-component", "set-object-reference"],
  "componentAdditions": [
    "RPGKingdom.Runtime.SomeSystem.SomeReviewedAdapter"
  ],
  "componentRemovals": [],
  "dependency": "optional operator-facing dependency or next action"
}
-->
```

For `mode: "known"`, Supervisor compares the requested tier, operation kinds, component additions, and component removals with the project-owned capability contract **before Codex starts**. A known mismatch removes the normal dispatch path through the existing Unity halt semantics, records durable preflight evidence, and reports the exact unsupported requirement and recommended dependency action. It must not launch an expensive implementation worker merely to rediscover the mismatch.

Preflight evidence is retained under:

`$RPGK_SUPERVISOR_STATE_ROOT/structural-authoring-preflight/GH-N.json`

The evidence includes the authorization tier, project contract path/revision/schema version, declared requirements, supported/unsupported result, and recommended next action. Halt comments surface the same diagnosis in GitHub, which makes it visible through normal issue diagnostics/dashboard views.

### Two-phase / deferred structural work

A feature may introduce a new component type that cannot be allowlisted until the source type exists on `main`. That is not permission to self-authorize the type from a feature branch.

Declare the source phase explicitly:

```markdown
<!-- symphony-scene-authoring-requirements
{
  "mode": "deferred",
  "tier": "mechanical-structural",
  "sourcePhaseOnly": true,
  "operations": ["add-component"],
  "dependency": "merge concrete source types, then land a bounded allowlist extension"
}
-->
```

For deferred work, Supervisor may continue the source/validation phase when the already-known tier/operation kinds are supported, but it intentionally withholds the structural authoring receipt. Therefore the worker cannot use `unity-author.sh` for production structural wiring during that lifetime even though the issue remains classified as structural work. After the new types merge, a separate bounded RPG Kingdom allowlist extension updates the executor/capability contract. The feature can then be explicitly rearmed with `mode: "known"` requirements for final scene wiring.

The required lifecycle is:

```text
Phase A: feature implements new source/component types, sourcePhaseOnly=true
    -> PR/review/human merge
Phase B: separate project allowlist issue reviews those concrete types/prerequisites
    -> PR/review/human merge
Phase C: feature requirements become mode=known
    -> explicit rearm -> production scene wiring -> fresh validation -> PR completion
```

Legacy Tier-2 issues created before this metadata contract remain diagnosable as `unknown` and retain their prior authorization behavior. New or materially revised Tier-2 issues should always carry the explicit requirements block so unsupported work can be stopped before model dispatch.


### New-scene composition

The new-scene lane is deliberately different from Tier 2. It does **not** broaden mutation authority for an established scene.

A new-scene issue must carry `authoring:scene-new-composition` and an explicit supported requirements block, for example:

```markdown
<!-- symphony-scene-authoring-requirements
{
  "mode": "known",
  "tier": "new-scene-composition",
  "operations": [
    "copy-scene",
    "set-transform",
    "reparent-object",
    "delete-object",
    "instantiate-existing-prefab"
  ]
}
-->
```

`copy-scene` is validated from the project-owned `newSceneComposition.creationOperationKind` capability; Supervisor does not maintain a duplicate operation allowlist.

The first request includes both `sourceScene` and the absent target scene:

```json
{
  "protocolVersion": 1,
  "tier": "new-scene-composition",
  "sourceScene": "Assets/RPGKingdom/Scenes/VerticalSlice.unity",
  "scene": "Assets/RPGKingdom/Scenes/PlaytestScene.unity",
  "operations": [
    {
      "kind": "set-transform",
      "objectPath": "World",
      "localPosition": {"x": 0, "y": 0, "z": 0},
      "localEulerAngles": {"x": 0, "y": 0, "z": 0},
      "localScale": {"x": 1, "y": 1, "z": 1}
    }
  ]
}
```

After successful initial copy-back, Supervisor writes host-owned provenance under:

`$RPGK_SUPERVISOR_STATE_ROOT/new-scene-provenance/GH-N.json`

That record binds the issue, workspace, branch, source scene, and target scene. Later requests omit `sourceScene` and may edit only that exact target. The target must remain absent from `origin/main`; once it becomes an established scene on main, this tier stops being valid for it.

Initial copy-back always includes the target `.unity` and its generated `.meta`. Later copy-back always includes the recorded target `.unity`. The source scene is hashed before/after staged Unity execution and must remain unchanged.

A reviewed generated-asset exception exists only for new-scene composition operations that must persist navigation output. The project executor must explicitly attest every such path in `generatedAssets`, every generated path must also appear in `changedAssets`, and Supervisor accepts them only under:

```text
Assets/RPGKingdom/Navigation/Generated/
```

The host copies back exactly the required target-scene asset(s) plus that executor-attested navigation manifest as one rollback-capable publish transaction. Missing staged files, duplicate manifest paths, scene files masquerading as generated assets, paths outside the reviewed root, or any unrelated changed asset fail closed. Existing mechanical and mechanical-structural tiers still prohibit generated-asset copy-back.

This exception is intentionally narrow: it exists for reviewed NavMesh/navigation generation required by an issue-owned new scene. It is not generic multi-asset copy-back.

This lane is intended for bounded creation/composition of a new authored scene such as `PlaytestScene`. It is not a general-purpose creative RPC, prefab editor, terrain-data generator, or back door for modifying existing production scenes.

## Worker interface

Create a temporary JSON request outside tracked source files and call:

```bash
bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/unity-author.sh" apply \
  --request /tmp/rpgk-authoring.json
```

### Tier-1 example

```json
{
  "protocolVersion": 1,
  "tier": "mechanical",
  "scene": "Assets/RPGKingdom/Scenes/VerticalSlice.unity",
  "operations": [
    {
      "kind": "set-string",
      "objectPath": "Village/ResourceNode_Wood_02",
      "componentType": "StableIdentity",
      "propertyPath": "stableId",
      "stringValue": "resource-node-wood-vertical-slice-02"
    },
    {
      "kind": "set-object-reference",
      "objectPath": "Enemy_DungeonApproach_Rustfang",
      "componentType": "BasicAIDebugPanel",
      "propertyPath": "forcedTarget",
      "target": {
        "objectPath": "PlayerCharacter/BasicAITarget",
        "componentType": "BasicAITarget"
      }
    }
  ]
}
```

Tier 1 supports the typed non-structural operations exposed by the RPG Kingdom executor, currently:

- `set-string`
- `set-int`
- `set-float`
- `set-bool`
- `set-enum-index`
- `set-object-reference`
- `set-object-reference-list`

All targets must already exist. Component/property resolution must be exact and unambiguous. Transform mutation and structural serialized properties remain forbidden.

### Tier-2 example

A Tier-2 request uses the distinct request tier:

```json
{
  "protocolVersion": 1,
  "tier": "mechanical-structural",
  "scene": "Assets/RPGKingdom/Scenes/CharacterSandbox.unity",
  "operations": [
    {
      "kind": "add-component",
      "objectPath": "Hostiles/Enemy_01",
      "componentType": "RPGKingdom.Character.CharacterState"
    }
  ]
}
```

The example illustrates the protocol shape only. The RPG Kingdom project-side executor remains authoritative for which structural operation kinds and component types are actually allowed. Supervisor deliberately does not expose arbitrary reflection, arbitrary C# execution, generic hierarchy mutation, or a general-purpose editor RPC.

Tier 2 may use reviewed structural operations and the existing typed configuration operations in the same request when the project executor supports that sequence. A request cannot broaden its own authority beyond the project-side allowlist.

Object names may be used only when unique in the loaded scene. Use the full hierarchy path when a name is ambiguous.

## Transaction boundary

1. Supervisor validates the request envelope and exact dispatch authorization.
2. For explicit Tier-2 requirements, Supervisor compares the declared capability requirements with the project-owned checked-in capability contract before launching Codex.
3. Source `Assets`, `Packages`, and `ProjectSettings` are mirrored to the persistent Windows staging project.
4. The Windows runner independently accepts only the known exact request tiers.
5. Existing-scene tiers open and mutate the requested scene through the reviewed RPG Kingdom Editor executor.
6. New-scene composition either copies the authorized source into an absent target or reopens the host-recorded issue-owned target.
7. Unity writes a structured result naming the changed asset set.
8. Existing-scene tiers require exactly one changed scene.
9. New-scene composition requires the target scene assets plus, when present, the executor's exact reviewed `generatedAssets` navigation manifest under `Assets/RPGKingdom/Navigation/Generated/`.
10. Supervisor verifies source-scene hashes for the new-scene tier and **only after all checks pass** performs rollback-capable bounded copy-back.

A failed or timed-out authoring operation leaves the source scene untouched. Staging is disposable. Existing-scene tiers do not permit additional staged assets to be copied back, and new-scene composition does not permit generated assets outside the reviewed navigation root.

## Validation and handoff

Authoring success is not implementation completion. After copy-back, the worker must inspect the resulting source diff and run the narrowest relevant EditMode/PlayMode validation through `unity-runner.sh`. Normal fresh-validation Git handoff, independent automated review, and human merge approval remain required.

Authoring evidence is retained under `Logs/SymphonyUnityAuthoring/<request-id>/` and request/response broker evidence under the ignored `Logs/SymphonyUnity/.author-broker/` tree. Broker active-request evidence includes the requested tier to make authorization decisions diagnosable.

## Non-goals

- making Supervisor authoritative for RPG Kingdom's structural allowlist;
- inferring structural requirements or permission from arbitrary natural-language issue prose;
- auto-adding component types to the RPG Kingdom executor;
- feature branches self-authorizing new component types;
- arbitrary Unity command execution;
- arbitrary C# or reflection RPC;
- direct `.unity`/`.prefab` YAML editing;
- Tier-3 creative or visual authoring;
- generic create/delete/reparent/transform authority for established scenes; the separate new-scene tier may expose reviewed spatial operations only for its issue-owned unmerged target;
- copying arbitrary staged assets back to source;
- bypassing RPG Kingdom scene-authoring policy or the exclusive Unity resource lease.
