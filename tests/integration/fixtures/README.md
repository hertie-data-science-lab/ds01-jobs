# Integration test fixtures

Each subdirectory of `scenarios/` is a self-contained job that the integration suite submits to ds01-jobs. The directories here are the **source-of-truth**; they're published to `fixtures/<name>` orphan branches on origin so the SUT (which only accepts GitHub URLs) can clone them.

## Scenarios

| Directory | Branch on origin | Used by |
|---|---|---|
| `smoke/` | `fixtures/smoke` | `tests/integration/test_lifecycle.py` (default) and `scripts/run-test-suite.sh` |
| `cpu-quick/` | `fixtures/cpu-quick` | `run-test-suite.sh` test 1 |
| `long-running/` | `fixtures/long-running` | `run-test-suite.sh` test 2 |
| `multi-file/` | `fixtures/multi-file` | `run-test-suite.sh` test 3 |
| `large-output/` | `fixtures/large-output` | `run-test-suite.sh` test 4 |
| `failing-runtime/` | `fixtures/failing-runtime` | `run-test-suite.sh` test 5 |
| `failing-build/` | `fixtures/failing-build` | `run-test-suite.sh` test 6 |
| `failing-base-image/` | `fixtures/failing-base-image` | preserved for future scanner-on-cloned-repo tests |
| `gpu-compute/` | `fixtures/gpu-compute` | `run-test-suite.sh` test 8 |
| `timeout/` | `fixtures/timeout` | `run-test-suite.sh` test 9 |

## Editing a fixture

1. Edit the files under `scenarios/<name>/`.
2. Commit on a feature branch + open a PR like any other change.
3. After the PR merges to `main`, run `scripts/sync-test-fixtures.sh --only <name>` to publish the change to the `fixtures/<name>` branch.

The sync script uses deterministic commits (fixed author and dates), so re-running with no fixture changes produces an identical SHA and is a no-op push.

## Adding a new scenario

1. Create `scenarios/<new-name>/` with a `Dockerfile` and any supporting files.
2. Add a row to the table above.
3. Wire it into `scripts/run-test-suite.sh` (or, in the future, the parametrized pytest suite).
4. Run `scripts/sync-test-fixtures.sh --only <new-name>` after the PR merges.

## Recovery

If any `fixtures/*` branches get deleted on origin:

```
scripts/sync-test-fixtures.sh                 # rebuild every branch
scripts/sync-test-fixtures.sh --only smoke    # rebuild one
```

The fixture trees on `main` are the source-of-truth — orphan branches on origin can always be reconstructed from them. Tests reference the branches by name (`--branch fixtures/<name>`) rather than SHA, so even non-deterministic rebuilds would be safe; the deterministic commits in the sync script are belt-and-braces.
