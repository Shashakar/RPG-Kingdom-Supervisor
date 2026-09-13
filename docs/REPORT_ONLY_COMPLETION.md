# Report-only / diagnostic completion

Some Symphony tasks are intentionally evidence-gathering work rather than repository implementation. A successful diagnostic should not be forced to create an empty commit or pull request, but ordinary implementation work must not be able to bypass the PR handoff merely because no diff exists.

## Explicit eligibility

Report-only completion is opt-in through the RPG Kingdom issue label:

- `completion:report-only`

The host does not infer this mode from the issue title/body, from a clean worktree, or from the absence of changes. Without that label, `git-handoff.sh report-complete` fails closed as `ReportOnlyNotAllowed`.

A successful report-only handoff adds:

- `symphony:report-complete`

and removes `symphony:ready` only after evidence verification succeeds. The issue remains open for human review/closure; Supervisor does not auto-close it.

## Worker command

A report-only worker submits its final durable report through the existing worker-facing Git handoff client:

```bash
bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/git-handoff.sh" report-complete \
  --report-body-file /tmp/rpgk-report.md \
  [--validation-run <unity-run-id> ...]
```

`--report-body` can be used instead of `--report-body-file` for short reports.

The client still uses the existing host-owned Git broker. Internally the request is a normal bounded handoff request with `completionMode=report-only`; the host wrapper routes it to the report verifier instead of the normal branch/commit/PR path.

## Host verification

Before creating the report-complete lifecycle state, the host verifies all of the following:

1. the request belongs to the current `GH-N` Symphony workspace;
2. the RPG Kingdom origin/workspace identity is valid;
3. the issue currently has `completion:report-only`;
4. the report body is non-empty and within the bounded host size limit;
5. the source workspace is clean;
6. `HEAD` exactly matches `origin/main`, so previously committed implementation work cannot be hidden behind report-only completion;
7. if `validation:unity-required` is present, supplied Unity run IDs resolve to **fresh, trustworthy, non-zero executed test evidence**;
8. on a reviewed continuation, required Unity evidence is newer than the previous `.symphony-attempt-complete` boundary.

Any source/test change or commit beyond `origin/main` must use the normal PR handoff path.

### Diagnostic Unity evidence is allowed to be red

For report-only work, `validation:unity-required` means the diagnostic must actually execute Unity and provide fresh trustworthy evidence. It does **not** mean the observed tests must all pass.

That distinction is necessary because a failed suite can be the result the report is supposed to establish. A full regression baseline, flaky-test investigation, or failure inventory would otherwise be structurally unable to complete whenever it successfully found a real failure.

Report-only Unity evidence therefore requires:

- a real Supervisor-generated `summary.json` for every supplied run ID;
- freshness relative to the reviewed-continuation boundary when applicable;
- a non-zero executed test count;
- valid test counts and a terminal result recorded by the runner.

`Passed` and trustworthy `Failed(Child)` results can both be reported. `NoTestsMatched`, zero-test artifacts, missing/unreadable summaries, or stale evidence are not accepted.

This exception is **only** for explicit `completion:report-only` work. Normal implementation/repair PR handoff still uses the strict Unity validator and requires a passing non-zero result when `validation:unity-required` is present.

## Continuation behavior

Report-only workers intentionally keep the source workspace clean, so Git-diff progress is not a meaningful between-turn signal. Their normal lifecycle is often:

```text
collect validation/artifacts
-> inspect evidence
-> synthesize report
-> report-complete
```

The artifact-analysis and report-synthesis steps may make no host-observable source or Unity change. The continuation policy therefore allows an explicit report-only worker to keep using its normal route-specific automatic turn budget while the workspace remains source-clean and quota remains healthy. A dirty or changed source workspace still stops automatic continuation, and the normal route/hard turn limits still apply.

See `docs/CONTINUATION_POLICY.md` for the full continuation contract.

## Durable evidence

The host posts the worker's report as a GitHub issue comment and appends a machine-readable marker derived from:

- issue number;
- report body;
- validation run IDs;
- current repository HEAD.

That digest makes a repeated `report-complete` call idempotent: the same report/evidence reuses the existing issue comment rather than posting duplicates.

The durable report comment also includes each supplied Unity run's platform, result, passed/total counts, and filter so a red diagnostic result is not visually presented as green validation.

After the durable comment exists, the host writes a trusted receipt under Supervisor-owned state:

```text
~/.local/state/rpg-kingdom-supervisor/report-completions/GH-N.json
```

The receipt contains the report digest, comment ID/URL, validated Unity evidence, repository HEAD, and the prior-attempt boundary. It is outside the model-writable issue workspace and therefore can be used for host reconciliation.

## Lifecycle mutation and partial-failure recovery

Normal success order is:

1. verify eligibility/workspace/evidence;
2. create or find the durable report comment;
3. persist the host-only verified receipt;
4. add `symphony:report-complete`;
5. remove incompatible implementation/review lifecycle labels;
6. remove `symphony:ready` last;
7. mark the receipt completed.

If GitHub mutation fails after step 3, `after-run-guard.sh` checks the trusted receipt before applying the generic implementation halt rule. The receipt is accepted only when its issue number, repository HEAD, and prior-attempt boundary match the worker lifetime that is ending. The guard then re-verifies the durable GitHub comment and `completion:report-only` eligibility and retries the lifecycle transition.

If reconciliation still cannot complete, the worker lifetime remains locally consumed but Supervisor deliberately does **not** convert the verified report into `symphony:halted`. The failure is surfaced for operator review instead.

A stale receipt from an earlier worker lifetime is not sufficient because its saved prior-attempt boundary will not match the current lifetime.

## Unity-required report tasks

Report-only mode does not weaken the requirement to actually use Unity when `validation:unity-required` is present. At least one fresh trustworthy non-zero Unity run must be supplied. The result may be passing or failing because the diagnostic outcome itself is the report subject. Reviewed continuations cannot recycle Unity evidence from before the previous attempt marker.

If the issue has no Unity-required label, report-only completion may legitimately contain no Unity run IDs.

## Human review

`symphony:report-complete` means the automation contract is complete, not that the GitHub issue has been automatically closed. A human can review the durable report comment and close the issue when appropriate. There is no automated reviewer/repair loop because there is no implementation branch or PR to review.

## Non-goals

Report-only completion does not:

- create an empty commit or dummy PR;
- infer success from a missing diff;
- permit dirty or source-changing workspaces;
- broaden Codex GitHub/network permissions;
- auto-close the issue;
- auto-merge anything;
- allow missing, stale, zero-test, or malformed Unity evidence;
- weaken passing-Unity requirements for normal implementation/repair PR handoff;
- replace the normal PR handoff for implementation work.
