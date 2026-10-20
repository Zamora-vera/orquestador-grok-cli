# Benchmarks

Twenty verifiable coding tasks executed through Grok CLI with the Deep Ultra
router on 2026-10-20. Each task runs in a temporary copy; hidden verification
files are restored before pytest runs, so editing tests cannot fake a pass.

## Method

1. Copy `setup/` to a temporary directory.
2. `grok -p "<prompt>" -m deep-ultra --yolo --max-turns N --cwd <tmp>`.
3. Copy `verify/` over the workspace.
4. `python -m pytest -q`; pass means exit code 0.
5. Read cost, tokens and cache hits from the ledger around the run.
6. Stop before the next task when the cumulative run cost reaches `--max-usd`.

Every task was verified to fail before the agent ran (broken setup + hidden
tests). The suite covers fixes, features, multi-file changes, debugging, path
and SQL security, thread safety and a small inventory app.

## Run A — 20 tasks, 2026-10-20

| Task | Category | Difficulty | Seconds | USD | Pass |
|---|---|---|---|---|---|
| fix_add | fix | easy | 13.2 | 0.001375 | yes |
| fix_max | fix | easy | 18.0 | 0.001324 | yes |
| fix_reverse | fix | easy | 17.6 | 0.001365 | yes |
| fix_count | fix | easy | 14.3 | 0.001388 | yes |
| feature_slug | feature | medium | 16.1 | 0.001625 | yes |
| feature_duration | feature | hard | 17.9 | 0.001609 | yes |
| feature_chunk | feature | medium | 15.7 | 0.001392 | yes |
| feature_title | feature | medium | 22.4 | 0.001283 | yes |
| multifile_user | multi-file | medium | 15.4 | 0.001416 | yes |
| multifile_config | multi-file | medium | 13.8 | 0.001451 | yes |
| multifile_repo | multi-file | medium | 15.4 | 0.001324 | yes |
| debug_index | debug | medium | 15.5 | 0.001383 | yes |
| debug_import | debug | medium | 14.8 | 0.001530 | yes |
| debug_types | debug | medium | 17.6 | 0.001418 | yes |
| security_path | security | hard | 20.5 | 0.001607 | yes |
| security_sql | security | medium | 21.8 | 0.002148 | yes |
| security_secret | security | easy | 14.6 | 0.003303 | yes |
| concurrency_counter | concurrency | hard | 17.1 | 0.001425 | yes |
| concurrency_sum | concurrency | hard | 19.1 | 0.002310 | yes |
| real_inventory | app | hard | 15.6 | 0.001575 | yes |

**Aggregate:** 20/20 passed (100%), **$0.032251**, **$0.001613 per task**,
1,339,624 prompt tokens of which **1,218,560 were cache reads (91.0%)**, 13,263
completion tokens.

## Run B — repeat of eight tasks

Eight tasks across all categories were run a second time: **8/8 passed**,
**$0.012187**, 555,255 prompt tokens, **508,928 cached (91.6%)**, 5,689
completion tokens.

A second full pass was started but interrupted when the local router process was
terminated by Windows (`0xC000013A`, console control event) after a few calls.
No task failed because of it. A watchdog task now restarts the router every five
minutes, and the interrupted pass's spend is included in the totals below.

## Cost of this phase

| Concept | USD |
|---|---|
| Run A | 0.032251 |
| Run B | 0.012187 |
| Interrupted pass + probes | 0.002579 |
| **Additional spend** | **0.046865** |
| Authorized cap | 0.100000 |

## Caveats

- Single-shot runs for 12 tasks; only eight were repeated. This is not a
  statistically meaningful comparison.
- No scores for Claude Code or Astra are claimed or inferred.
- Provider cache pricing dominates at this context size (each task sends the
  CLI's ~16k-token system prompt); a cold cache costs more.
- The tasks are small and self-contained. None requires a large existing
  codebase or a multi-hour refactor.

## Run

```powershell
deep-ultra bench --tasks benchmarks/tasks --max-usd 0.09 --out benchmarks/results.json
deep-ultra bench --tasks benchmarks/tasks --only fix_add,security_path
```
