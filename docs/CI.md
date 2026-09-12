# Supervisor CI

The Supervisor's deterministic regression gate is the same command locally and in GitHub Actions:

```bash
bash tests/run.sh
```

The workflow is defined in `.github/workflows/supervisor-tests.yml` and runs automatically for:

- pull requests targeting `main`;
- pushes to `main`;
- manual `workflow_dispatch` runs.

The workflow job has the stable check name `supervisor-tests` so it can be selected as a required branch-protection check later.

## CI boundary

CI intentionally validates only the deterministic Supervisor regression suite. It does not start Symphony, spend Codex allowance, run Unity, invoke WSL-to-Windows host integration, or require live GitHub/OpenAI credentials.

The workflow uses a checkout without persisted GitHub credentials, grants only read access to repository contents, sets up Python explicitly, and clears Supervisor/account credential and host-override environment variables before invoking `bash tests/run.sh`.

Host-only capabilities may be reported as `SKIP` by tests when they are unavailable on the Linux runner. A skip is acceptable only where the deterministic test already defines that host capability as optional; the canonical suite must still finish with:

```text
supervisor-tests: PASS
```

Do not add path filters unless it is proven they cannot skip changes to shared scripts, fixtures, policy, or test infrastructure. Correctness of the regression gate takes priority over saving a small amount of runner time.

## Local verification

Before opening or approving a Supervisor PR, operators can run the exact same regression gate locally:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash tests/run.sh
```

A local developer shell may contain credentials or host-specific overrides that CI does not. Tests must not rely on those values unless the test itself supplies an explicit fixture. If a test passes only because of an operator's shell state, fix the test rather than adding secrets or host integration to CI.
