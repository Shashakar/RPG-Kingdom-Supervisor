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
