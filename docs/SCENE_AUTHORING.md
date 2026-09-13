# Unity Scene Authoring

## Purpose

Supervisor provides a narrow host-owned seam for deterministic production-scene changes that RPG Kingdom explicitly classifies as an approved scene-authoring tier. The model chooses a typed request; Unity performs it through reviewed RPG Kingdom Editor code; Supervisor owns authorization, Windows/Unity execution, and copy-back into the GH workspace.

This does not grant general production-scene authority or creative scene composition.

## Tiers and authorization

Scene-authoring authority is dispatch-scoped, issue-scoped, workspace-scoped, and exact-tier matched.

| Issue label | Request tier | Scope |
| --- | --- | --- |
| `authoring:scene-mechanical` | `mechanical` | Tier 1: non-structural serialized mechanical configuration |
| `authoring:scene-structural` | `mechanical-structural` | Tier 2: separately reviewed, bounded structural operations implemented and allowlisted by RPG Kingdom, plus typed configuration |

An authoring issue must also own `resource:unity-editor`. When validation is required it must carry the normal `validation:unity-required` label.

The two authoring labels are mutually exclusive. Tier-1 authority does not grant Tier-2 authority, and Tier-2 authority does not implicitly grant Tier-1 requests. The Unity preflight writes the exact authorized request tier into a host-owned receipt for that worker lifetime. The client, broker, host adapter, and Windows staging runner reject unknown tiers; the broker and host additionally require the request tier to exactly match the receipt.

Structural authority is never inferred from issue prose, model intent, or possession of the Unity resource.

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
2. Source `Assets`, `Packages`, and `ProjectSettings` are mirrored to the persistent Windows staging project.
3. The Windows runner independently accepts only the known Tier-1/Tier-2 request tiers.
4. Unity opens and mutates the requested scene through the reviewed RPG Kingdom Editor executor.
5. Unity writes a structured result naming the changed asset set.
6. Supervisor requires that changed set to exactly equal the one requested scene.
7. **Only after all checks pass** does the Windows host adapter atomically replace that scene in the GH source workspace.

A failed or timed-out authoring operation leaves the source scene untouched. Staging is disposable. Tier 2 does not permit additional staged assets to be copied back.

## Validation and handoff

Authoring success is not implementation completion. After copy-back, the worker must inspect the resulting source diff and run the narrowest relevant EditMode/PlayMode validation through `unity-runner.sh`. Normal fresh-validation Git handoff, independent automated review, and human merge approval remain required.

Authoring evidence is retained under `Logs/SymphonyUnityAuthoring/<request-id>/` and request/response broker evidence under the ignored `Logs/SymphonyUnity/.author-broker/` tree. Broker active-request evidence includes the requested tier to make authorization decisions diagnosable.

## Non-goals

- arbitrary Unity command execution;
- arbitrary C# or reflection RPC;
- direct `.unity`/`.prefab` YAML editing;
- Tier-3 creative or visual authoring;
- generic create/delete/reparent/transform operations unless a future reviewed project-side Tier-2 contract explicitly adds a bounded operation;
- copying arbitrary staged assets back to source;
- bypassing RPG Kingdom scene-authoring policy or the exclusive Unity resource lease.
