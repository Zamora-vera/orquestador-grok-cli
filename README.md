# Deep Ultra

A **user-operated** Python coding assistant CLI with deterministic model routing,
DeepSeek and Kimi adapters, a persistent SQLite TTL cache, local key/value memory,
repository file listing, optional image input, and a restricted pytest runner.

This is **not yet a Grok CLI plugin or an autonomous coding agent**. It does not
edit files, run multistep tool loops, orchestrate agents, or deploy. Grok CLI
integration needs its actual extension protocol.

## Status: v0.3.0

Changes in this branch:

- Model IDs and prices verified against official pages on 2026-10-20.
- Routing now uses the full difficulty scale instead of a binary flag.
- `max_tokens` is bounded and forwarded to the provider, so answers cannot run away.
- One retry policy (two short backoffs) and same-provider fallback only.
- Cache keys include `max_tokens`, so a short answer is never reused for a long request.
- Cache entries can be purged; the CLI reports whether an answer came from cache or the API.
- FinOps engine: dated price catalog, peak/off-peak tariffs, budgets, ledger, alerts,
  loop guard, deferral, and separate real/estimated savings.

## Verified models

Checked against the official documentation on 2026-10-20:

| Model | Provider | Version | Context | Vision | Tier | Key |
|---|---|---|---|---|---|---|
| `deepseek-flash` | DeepSeek | V4.1-Flash-1017 | 1M | yes | 1 | `DEEPSEEK_API_KEY` |
| `deepseek-v4-pro` | DeepSeek | V4-Pro-0813 | 1M | no | 2 | `DEEPSEEK_API_KEY` |
| `kimi-k3` | Kimi | kimi-k3 | 1M | yes | 3 | `KIMI_API_KEY` |
| `kimi-k2.7-code` | Kimi | kimi-k2.7-code | 256K | yes | 2 | `KIMI_API_KEY` |
| `kimi-k2.6` | Kimi | kimi-k2.6 | 256K | yes | 2 | `KIMI_API_KEY` |

`deepseek-v4-pro` is a different build (V4-Pro-0813), not an alias of Flash.
`deepseek-chat` and `deepseek-reasoner` are not in this account's catalog.
`kimi-k2.5` was discontinued on 2026-08-31.

## Install

```bash
python -m pip install -e '.[test]'
export DEEPSEEK_API_KEY='...'    # never commit real keys
# export KIMI_API_KEY='...'     # only when using --image or --model kimi-k2.5
deep-ultra models
deep-ultra chat 'Explain this algorithm'
deep-ultra chat 'Review this architecture' --repo .
deep-ultra chat 'Describe this screenshot' --image screenshot.png
deep-ultra chat 'hello' --max-tokens 512 --ttl 600
deep-ultra test .
pytest -q
```

On Windows, set keys for the current shell with `$env:DEEPSEEK_API_KEY = '...'`.
Never paste keys into the chat, code, logs or issues.

## Routing

`difficulty()` scores prompt length and hard keywords (architecture, refactor,
security, concurrency, migration, debug) on a 0–3 scale.

- Score 0–1: `deepseek-flash` (cheapest).
- Score 2–3: `deepseek-v4-pro` (different build, no vision).
- Images: `deepseek-flash` handles them, so the router does not escalate to Kimi.
- Provider order: DeepSeek first. Kimi is considered only when no DeepSeek model
  fits, and its unverified prices currently block automatic selection.
- `--model` overrides routing after checking key, modality, context and pricing.

Routing is a transparent heuristic, not a quality guarantee.

## FinOps engine

Prices live in `src/deep_ultra/catalog.json`, with the source URL and the date
checked. Nothing is treated as permanent: `deep-ultra refresh` compares the
catalog with each provider's live `/v1/models` (no billing) and exits 1 on drift.

| Model | Peak cache-hit / in / out | Off-peak cache-hit / in / out |
|---|---|---|
| `deepseek-flash` | $0.006 / $0.300 / $1.200 | $0.003 / $0.150 / $0.600 |
| `deepseek-v4-pro` | $0.044 / $1.320 / $3.960 | $0.022 / $0.660 / $1.980 |

USD per 1M tokens, from the official DeepSeek pricing page (2026-10-20).
Off-peak is exactly half of peak.

**Peak** is 01:00–04:00 and 06:00–10:00 UTC, Monday–Friday, excluding Chinese
public holidays. Everything else is off-peak, including weekends and holidays in
full. Holiday dates come from the `holidays` package (CN calendar).

**Deferral.** Interactive requests never wait. `--defer --max-wait SECONDS` moves
deferrable work to the next off-peak instant and refuses when the wait exceeds the
cap.

**Budgets.** `DEEP_ULTRA_TASK_BUDGET_USD`, `DEEP_ULTRA_DAILY_BUDGET_USD`,
`DEEP_ULTRA_ALERT_PCT`; `--budget-usd` overrides the task budget. A worst-case
estimate is checked before every call, so a call that would cross a limit is
blocked instead of executed.

**Loop guard.** `DEEP_ULTRA_MAX_CALLS_PER_TASK` (default 25) and
`DEEP_ULTRA_MAX_REPEATS` (identical prompts, default 3).

**Savings.** The ledger stores every call and cache hit with tokens, cost and
tariff:

- **Real savings**: an exact local cache hit avoided a call; the stored cost of
  the original call is reported.
- **Estimated savings**: provider cache discount plus the off-peak discount
  versus peak.

They are reported separately by `deep-ultra usage`.

**Provider cache and context reuse.** The system prompt and the repository file
list are a stable prefix, so repeated calls hit DeepSeek's automatic prefix cache.
Local cache keys include model, messages, temperature and `max_tokens`.

**Kimi.** IDs and capabilities come from the official model list. The pricing
tables are rendered client-side and could not be read programmatically, so every
Kimi entry stays `pricing_verified: false` and the router refuses to spend on it.
To enable Kimi, point `DEEP_ULTRA_CATALOG` at a catalog file with prices from your
console. RPM/TPM/TPD depend on the account's cumulative recharge tier and are not
assumed by the code.

Commands: `deep-ultra models`, `validate`, `refresh`, `budget`, `usage --days N`.

## Security

- Credentials come only from environment variables.
- Image uploads are capped at 8 MB and limited to png/jpg/jpeg/webp.
- `deep-ultra test` runs pytest only; any other command is rejected.
- No file editing, shell execution or deployment from the assistant.
- `.env`, SQLite files and virtualenvs are ignored by git.

## Tests

`pytest -q` covers routing, overrides, missing keys, vision gating, cache hits
without network, `max_tokens` forwarding, same-provider fallback, provider
failure reporting, cache-key determinism, file filtering and the pytest allowlist.

## Roadmap

1. Grok CLI integration through its documented extension interface (next phase).
2. Streaming and tool calls; capability discovery from `/v1/models`.
3. Token/cost telemetry per call and per session.
4. Multi-provider failover with explicit user approval, circuit breakers, rate limits.
5. Benchmarks on public coding tasks and CI on every PR.
