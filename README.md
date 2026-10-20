# Deep Ultra — initial working prototype

A **user-operated** Python coding assistant CLI featuring deterministic context routing, DeepSeek and Kimi adapters, local SQLite TTL cache, persistent key/value memory, repository file indexing, image input where supported, and a restricted pytest runner.

This is **not yet a Grok CLI plugin or an autonomous coding agent**. It does not edit files, perform multistep reasoning/tool use, orchestrate autonomous agents, or deploy. Grok CLI integration requires its actual integration protocol. Vision support depends on the provider/model/account supporting the selected model. The default Kimi ID/endpoint should be verified against your account documentation; model availability changes. No live provider API call was run without credentials. Performance claims versus other assistants are not established.

## Install

```bash
python -m pip install -e '.[test]'
export DEEPSEEK_API_KEY='your-key'
export KIMI_API_KEY='your-key'
deep-ultra models
deep-ultra chat 'Explain this algorithm'
deep-ultra chat 'Review this architecture' --repo .
deep-ultra chat 'Describe this screenshot' --image screenshot.png
deep-ultra test .
pytest -q
```

API credentials are loaded only from environment variables; never commit secrets. The program sends user prompts and *file names* (not repository file contents) to the selected provider when `--repo` is used. An image supplied with `--image` is sent to the remote provider. Do not send sensitive content without permission.

## Routing & costs

Simple requests select a lower tier when possible; harder ones prefer reasoning-capable models. Routing is a transparent heuristic, **not a guarantee of quality**. TTL-cached identical inputs avoid subsequent API calls (up to 100% call reduction for exact cache hits during TTL), but new prompts still cost money. A SQLite on-disk cache survives process restarts. Quality, latency, and cost estimation/telemetry and a second cache layer are future tasks.

## Production roadmap

1. Verify current provider model discovery and pricing; implement robust capability negotiation and streaming/tool calls.
2. Integrate using Grok CLI's documented extension interface; add agent loop, file editing patches, safe approvals, and versioned workspaces.
3. Add multi-provider failover (with approval to share data externally), circuit breakers and rate limits; research LiteLLM Router and RouteLLM as **optional** components.
4. Add full-text/search/semantic repository index with privacy exclusions, memory namespaces and encryption.
5. Add benchmarks on public coding tasks, test integration and vision, token/cost telemetry, secret scanning and CI.
6. Open PR, require passing CI, manual review, then integrate; no direct production deployment.
