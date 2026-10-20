# Benchmarks

Small, verifiable coding tasks executed through Grok CLI with the Deep Ultra
router on 2026-10-20. Every task is a temporary copy; the hidden verification
files are restored over the workspace before pytest runs, so editing tests
cannot fake a pass.

## Method

1. Copy `setup/` to a temporary directory.
2. Run `grok -p "<prompt>" -m deep-ultra --yolo --max-turns N --cwd <tmp>`.
3. Copy `verify/` over the workspace (fresh tests).
4. Run `python -m pytest -q`; pass means exit code 0.
5. Read spend, tokens and cache hits from the Deep Ultra ledger around the run.
6. Stop before the next task when the cumulative cost reaches `--max-usd`.

## Results

| Task | Difficulty | Model alias | Turns | Pass | Seconds | Cost USD | Prompt | Cached | Output |
|---|---|---|---|---|---|---|---|---|---|
| easy_fix | easy | deep-ultra | 5 | yes | 13.1 | 0.001272 | 65,240 | 59,776 | 455 |
| medium_slug | medium | deep-ultra | 5 | yes | 12.8 | 0.001334 | 65,502 | 59,904 | 524 |
| medium_multifile | medium | deep-ultra | 5 | yes | 13.7 | 0.001321 | 66,181 | 60,288 | 427 |
| hard_parse | hard | deep-ultra | 5 | yes | 13.3 | 0.001527 | 66,649 | 60,672 | 747 |

**Aggregate:** 4/4 passed (100%), **$0.005454** total, **$0.001364** average per
task, 263,572 prompt tokens of which **240,640 were cache reads (91.3%)**, 2,153
completion tokens.

All four tasks were solved by the automatic route (Flash). A direct request with
an architecture/security prompt routed to `deepseek-v4-pro` and cost $0.000079.

## Caveats

- Four synthetic tasks and a single run each. This is a smoke benchmark, not a
  statistically meaningful comparison.
- No scores for Claude Code or Astra are claimed or inferred. The numbers above
  are only Deep Ultra's own measurements.
- Provider cache pricing dominates the cost at this context size; a cold cache
  would cost more.
- The `hard_parse` task is logic-heavy but short; it does not exercise large
  multi-file refactors.

## Run

```powershell
deep-ultra bench --tasks benchmarks/tasks --max-usd 0.10 --out benchmarks/results.json
deep-ultra bench --tasks benchmarks/tasks --only easy_fix,hard_parse
```

## What would improve quality

1. Add a repository-content retrieval step (slices of files, not only names) for
   tasks that need code context; the current pack carries structure only.
2. Escalate on failures, not only on prompt keywords: a retry with Pro after a
   failed verification would likely fix tasks Flash cannot solve.
3. Grow the suite with multi-file refactors and a real test-fixing task, and run
   each task three times to report variance.
