# Experimental Unity CLI Contract Tests

Run with:

    bash tests/run-unity-cli-experimental.sh

These three test files are excluded from the required Supervisor
regression suite because their expected staged-editor broker contract
does not match the supported production broker operations.

Baseline verified October 17, 2026:
- unity-cli-readonly-test.py: 1 inherited failure
- unity-cli-stage-benchmark-test.py: 1 inherited failure
- unity-cli-staged-code-test.py: 3 inherited failures

The failures are unresolved and must not be considered passing.
Do not enable staged-editor operations or weaken authorization simply
to satisfy these assertions.

Reintroduce these tests into the required suite only after reviewing
and implementing an explicitly approved broker contract.

The production `scripts/unity-runner.sh` and `scripts/unity-host-broker.py`
reject all four editor-prefixed operations before any execution. The required
`tests/unity-runner-policy-test.sh` verifies these denial boundaries.

The normal GitHub Actions workflow runs this experimental suite as a separately
labeled, non-blocking step. It reports all three files and remains visibly
unsuccessful until the unsupported contracts are implemented and reviewed.
The step is not permission to enable arbitrary Unity Editor C# execution:
`unity-cli-staged-code.py` runs trusted C# as the Windows operator account
when explicitly enabled and is not a sandbox. The production deployment must
leave `RPGK_UNITY_STAGED_CODE_ENABLED` unset/disabled, with no worker access
to operator grants or promotion/copyback mechanisms.
