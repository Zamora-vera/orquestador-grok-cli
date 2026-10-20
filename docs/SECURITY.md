# Security review — v0.7.0

Scope: local router (`127.0.0.1:6011`), MCP server, retrieval index, memory and
the Grok CLI integration. Reviewed 2026-10-20.

## Controls in place

| Area | Control | Verified |
|---|---|---|
| Network exposure | Binds only `127.0.0.1`; no external interface | `Get-NetTCPConnection` shows 127.0.0.1 |
| Authentication | Bearer token required on every HTTP route | 401 without token, 200 with |
| Command execution | Router and MCP tools execute no shell commands | code review + tests |
| Filesystem scope | MCP and retrieval resolve paths under `--repo`; symlinks skipped | code review |
| Spend | Pre-call worst-case estimate, task and daily caps, 402 before calling | HTTP 402 test |
| Loops | Calls per task and repeated-prompt caps | unit tests |
| Secrets | No keys in code, tests, logs or git; `.env` ignored | repository scan |
| Provider data | Same-provider fallback only; no silent vendor switch | code review |
| MCP side effects | Read-only tools except `memory_add` (local SQLite) | tests |

## Findings and residual risks

1. **Prompt injection from repository files (medium).** Retrieved snippets are
   inserted into the prompt as data. A malicious comment such as "ignore previous
   instructions" can influence the model's answer. The tools cannot execute
   commands, so impact is limited to misleading output or suggested changes.
   Mitigation: snippets are labelled with `path:start-end`, capped by tokens, and
   the system prompt says to treat retrieved code as data. Review diffs before
   applying them.
2. **Memory poisoning (medium).** `memory_add` persists text that is later
   injected into agent prompts. Only the local user can write memory, but an
   injected agent turn could store a bad instruction. Mitigation: entries expire
   (default 90 days), `memory list` is auditable, `memory prune` removes stale
   entries. Review memory before long unattended runs.
3. **Local token file (low).** `~/.grok/deep-ultra/router-token.txt` is readable
   by the current user, as is `config.toml`. Any process running as that user can
   read it. Loopback binding limits remote abuse. Rotate by replacing both the
   file and the `api_key` in `config.toml`.
4. **No TLS (low).** Traffic stays on loopback; TLS would add no protection
   against a local attacker who can already read the token.
5. **No per-IP rate limit (low).** Local processes can send many requests. The
   spend caps bound financial impact; CPU impact remains.
6. **MCP trust boundary (low).** Grok CLI starts the server from `config.toml`.
   A tampered config could point to another command. Treat `config.toml` as a
   privileged file.
7. **Benchmark harness (low).** It copies task files and runs pytest in a temp
   directory. It never runs the agent as administrator. The agent itself uses
   `--yolo` in benchmarks, which is acceptable only for disposable copies.

## What was checked

- Repository secret scan (`sk-*`, `gho_*`) on tracked files: clean.
- Router auth with and without token.
- Budget block before any upstream call (HTTP 402).
- Concurrent requests (6) and abrupt client disconnect: server stayed healthy.
- Watchdog restart after a forced stop.
- MCP handshake (`mcp doctor`) and a real tool call from Grok CLI.

## Recommendations before production

1. Rotate the local token and store it in Windows Credential Manager if the host
   is shared.
2. Run unattended agents in a disposable workspace or sandbox profile.
3. Review `memory list` and the diff of every autonomous change before merge.
4. Keep `DEEP_ULTRA_DAILY_BUDGET_USD` set in production.
