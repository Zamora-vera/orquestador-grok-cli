# Deep Ultra

A **user-operated** Python coding assistant CLI with deterministic model routing,
DeepSeek and Kimi adapters, a persistent SQLite TTL cache, local key/value memory,
repository file listing, optional image input, and a restricted pytest runner.

This is **not yet a Grok CLI plugin or an autonomous coding agent**. It does not
edit files, run multistep tool loops, orchestrate agents, or deploy. Grok CLI
integration needs its actual extension protocol.

## Status: v0.2.0

Changes in this branch:

- Model IDs verified against the provider catalog on 2026-10-20.
- Routing now uses the full difficulty scale instead of a binary flag.
- `max_tokens` is bounded and forwarded to the provider, so answers cannot run away.
- One retry policy (two short backoffs) and same-provider fallback only.
- Cache keys include `max_tokens`, so a short answer is never reused for a long request.
- Cache entries can be purged; the CLI reports whether an answer came from cache or the API.

## Verified models

Checked with `GET /v1/models` on 2026-10-20:

| Model | Provider | Reasoning | Vision | Cost tier | Key |
|---|---|---|---|---|---|
| `deepseek-flash` | DeepSeek | no | no | 1 | `DEEPSEEK_API_KEY` |
| `deepseek-v4-pro` | DeepSeek | yes | no | 2 | `DEEPSEEK_API_KEY` |
| `kimi-k2.5` | Kimi (Moonshot) | yes | yes | 3 | `KIMI_API_KEY` |

`deepseek-chat` and `deepseek-reasoner` are **not** in this account's catalog;
the prototype's old IDs were replaced. The Kimi model ID and endpoint still need
confirmation against the account documentation before the first live call.

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
- Score 2–3: `deepseek-v4-pro` (reasoning).
- Images: only vision models; with only `DEEPSEEK_API_KEY` set, image requests fail
  with a clear error instead of silently dropping the image.
- `--model` overrides routing after checking that the key exists and vision is supported.

Routing is a transparent heuristic, not a quality guarantee.

## Cost controls

- **Exact-answer cache:** SQLite on disk, TTL 1 h by default (`--ttl`), survives restarts.
  A repeated prompt makes zero API calls.
- **Bounded output:** `--max-tokens` or `DEEP_ULTRA_MAX_TOKENS`, default 4096.
- **Bounded retries:** two short backoffs for 429/5xx/network errors; other 4xx fail fast.
- **Same-provider fallback only:** a prompt never reaches another vendor without approval.
- **No hidden calls:** one user prompt triggers at most one provider conversation.
- **Names-only context:** `--repo` sends file names, never contents.

The cache stores answers for a fixed TTL; delete
`~/.cache/deep-ultra/state.sqlite3` (or call `Cache.purge_expired`) to reclaim space.

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
