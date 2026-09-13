# Tier-1 Unity Scene Authoring

## Purpose

Supervisor provides a narrow host-owned seam for production-scene changes that RPG Kingdom classifies as **Tier 1 mechanical scene configuration**. The model chooses the requested deterministic change; Unity performs it through RPG Kingdom Editor code; Supervisor owns authorization, Windows/Unity execution, and copy-back into the GH workspace.

This does not grant general production-scene authority.

## Authorization

An issue must have both:

- `resource:unity-editor`
- `authoring:scene-mechanical`

The Unity preflight records a host-owned authorization receipt for that worker lifetime. The authoring broker independently requires the matching Unity lock, workspace, issue, and mechanical authorization before it will invoke Unity.

`authoring:scene-structural` is reserved for separately scoped Tier-2 work. The Tier-1 executor does not implement structural operations.

## Worker interface

Create a temporary JSON request outside tracked source files and call:

```bash
bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/unity-author.sh" apply \
  --request /tmp/rpgk-authoring.json
```

Example envelope:

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

Object names may be used only when unique in the loaded scene. Use the full hierarchy path when a name is ambiguous.

## Supported operations

The initial RPG Kingdom executor supports only:

- `set-string`
- `set-int`
- `set-float`
- `set-bool`
- `set-enum-index`
- `set-object-reference`
- `set-object-reference-list`

All targets must already exist. Component and property resolution must be exact/unambiguous.

Tier 1 rejects Transform mutation and structural serialized properties. It provides no operation for creating/deleting/reparenting GameObjects or adding/removing components.

## Transaction boundary

1. Source `Assets`, `Packages`, and `ProjectSettings` are mirrored to the persistent Windows staging project.
2. Unity opens and mutates the requested scene through `SerializedObject` / Editor scene APIs.
3. RPG Kingdom validates the entire typed request before the first mutation and saves only after all operations apply.
4. Unity writes a structured result naming the changed asset set.
5. Supervisor requires that changed set to exactly equal the one requested scene.
6. **Only after all of those checks pass** does the Windows host adapter atomically replace that scene in the GH source workspace.

A failed or timed-out authoring operation leaves the source scene untouched. Staging is disposable.

## Validation and handoff

Authoring success is not implementation completion. After copy-back, the worker must run the issue's focused EditMode/PlayMode validation through `unity-runner.sh`. Normal fresh-validation Git handoff, independent automated review, and human merge approval remain required.

Authoring evidence is retained under `Logs/SymphonyUnityAuthoring/<request-id>/` and request/response broker evidence under the ignored `Logs/SymphonyUnity/.author-broker/` tree.

## Non-goals

- arbitrary Unity command execution;
- arbitrary C# or reflection RPC;
- direct `.unity`/`.prefab` YAML editing;
- Tier-2 structural authoring;
- creative visual/level design;
- bypassing RPG Kingdom scene-authoring policy.
