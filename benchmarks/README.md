# Benchmarks

Twenty verifiable coding tasks run three times through Grok CLI with the Deep
Ultra router (2026-10-20), plus one task on a real open-source repository. Each
task runs in a temporary copy; hidden verification files are restored before
pytest runs, so editing tests cannot fake a pass.

## Method

1. Copy `setup/` to a temporary directory.
2. `grok -p "<prompt>" -m deep-ultra --yolo --max-turns N --cwd <tmp>`.
3. Copy `verify/` over the workspace.
4. `python -m pytest -q`; pass means exit code 0.
5. Read cost, tokens and cache hits from the ledger around the run.
6. Stop before the next task when the cumulative run cost reaches `--max-usd`.

Every task was verified to fail before the agent ran. Categories: fixes,
features, multi-file changes, debugging, path/SQL/secret security, thread safety
and a small inventory app.

## Three runs

| Run | Version | Passed | Cost USD | Mean s | Cache |
|---|---|---|---|---|---|
| A | v0.6.0 | 20/20 | 0.032251 | 16.82 | 91.0 % |
| B | v0.7.0 | 20/20 | 0.032070 | 16.10 | 91.2 % |
| C | v0.7.0 | 20/20 | 0.030265 | 16.68 | 91.3 % |
| **All** | — | **60/60** | **0.094586** | — | — |

Aggregate over 60 task runs: success **100 %**, p50 **15.9 s**, p95 **21.4 s**,
p50 cost **$0.001404**, p95 cost **$0.002343**, standard deviation 2.44 s and
$0.000392 per task.

**Regression check v0.6.0 → v0.7.0:** success unchanged at 100 %; mean cost
$0.001613 → $0.001558 (−3.4 %), mean time 16.82 s → 16.39 s (−2.6 %). Both
changes are within run-to-run noise; no regression was observed.

## Real repository

`pallets/click` (178 files) was cloned to a temporary directory. A real
regression was introduced in `src/click/formatting.py::wrap_text` by ignoring
`subsequent_indent`; the repository's own `tests/test_formatting.py` then failed
in 5 tests.

The agent found the cause, restored the parameter, and reported the result:
**37/37 tests passed** in the targeted file, plus the full suite (2179 passed,
107 skipped, 1 xfailed). Duration 61.5 s, cost **$0.002721**.

## Cost of this phase

| Concept | USD |
|---|---|
| Run B | 0.032070 |
| Run C | 0.030265 |
| Real Click task + MCP/stability probes | 0.006570 |
| **Additional spend** | **0.068905** |
| Authorized cap | 0.100000 |

Run A belongs to the previous phase and is used as the v0.6.0 baseline.

## Caveats

- Synthetic tasks are small and self-contained; the Click task covers a real
  codebase but a single function.
- No scores for Claude Code or Astra are claimed or inferred.
- Provider cache pricing dominates at this context size; a cold cache costs more.
- The 60 runs are three passes over the same 20 tasks, not 60 independent tasks.

## Run

```powershell
deep-ultra bench --tasks benchmarks/tasks --max-usd 0.035 --out benchmarks/results.json
deep-ultra bench --tasks benchmarks/tasks --only fix_add,security_path
```
