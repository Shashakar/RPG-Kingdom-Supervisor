# Scoped gameplay scene transaction — initial integration

Tracked by Supervisor #198 and implemented on the `codex/198-host-transaction-integration` branch.

## Authorization

The `authoring:scene-gameplay` issue label, combined with
`resource:unity-editor`, an explicit known-mode requirements contract, and
the project-owned supported capability manifest, permits only the reviewed
`opening-encounter-composition` tier / `compose-authored-opening-encounter`
operation in `Assets/RPGKingdom/Scenes/PlaytestScene.unity`.

Preflight writes a host-owned grant for issue, exact workspace/branch,
source revision, scene, operation and six fixed hierarchy roots. Broker and
host validate that grant before invoking the existing staged Unity authoring
runner. The Windows runner validates the exact typed operation and restricts
copy-back to the one scene. The existing Unity project executor is the only
allowed implementation, not an arbitrary Editor script.

## Boundaries

The fixed approved roots are:
- `World/TownArea/PlayerSpawnPoint`
- `World/TownArea/Enemy_FirstApproach_Scavenger`
- `Systems/Encounter_FirstApproach`
- `PlayerCharacter` (targeting reference owner only)
- `Systems/SaveLoadSystem` (encounter reference owner only)
- `Systems` (startup reference owner only)

The broad parent object `Systems` does **not** give the worker generic
permission to change every child. The project-owned typed Editor operation
must touch only its reviewed reference fields on existing systems.

Source revision mismatches or uncommitted code/assembly changes fail the
host gate. All other authoring tiers preserve their old behavior.

## Required validation before production use

- Supervisor CI, client/broker/preflight tests, and actual Windows PowerShell
  syntax/host stage tests must pass.
- An RPG Kingdom branch must contain and merge the reviewed typed operation
  and capability contract before this tier is used for production scene edits.
- Exercise wrong issue, branch, scene, operation, source revision, unexpected
  output, protected scene changes, and rollback with an end-to-end Unity
  test using a disposable stage.
- Fresh scene-level EditMode/PlayMode behavior verification must cover
  acquisition, pursuit, attacks, defeat, spawning, legacy Save/Load and
  existing village behavior, with human visual and merge sign-off.

Unity Editor is not an OS sandbox. The design avoids arbitrary C# execution
by pinning a reviewed project revision and a typed operation, and forbidding
worker-injected scripts at the host boundary. It does not prevent malicious
code already present in an approved revision; reviewer approval is still
necessary. A scene-only changed-assets manifest does not prove which
GameObjects changed, so review the project executor and produced scene diff.

## Unit tests

```sh
python3 tests/test_gameplay_scene_transaction_policy.py
python3 tests/test_gameplay_broker_authorization.py
python3 tests/structural-authoring-preflight-test.py
```
