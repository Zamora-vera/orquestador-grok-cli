# Changelog

## v1.0.0 — 2026-10-20

Grok CLI is the agent; Deep Ultra is the routing, context and cost engine.

### Changed

- Removed the redundant `deep-ultra agent` and `deep-ultra agents` commands.
  Agent loops, file edits and shell tools live in Grok CLI. `analyze`, `context`,
  memory, `bench`, `mcp` and the router remain.
- Router token moved from a text file to Windows Credential Manager (via
  `keyring`). `~/.grok/config.toml` now uses
  `env_key = "DEEP_ULTRA_ROUTER_TOKEN"` for the Deep Ultra model, so the token is
  no longer written to the config.
- New `deep-ultra token rotate|show|set|status|delete` commands.
- New `~/.grok/deep-ultra/grok-du.ps1` wrapper: loads the token from the
  credential store into the environment and runs Grok CLI.
- Router and watchdog load the token at startup; the token file was removed.

### Security

- No router token in the repository, config, logs or plaintext files.
- MCP tools remain read-only except `memory_add`.
- The benchmark harness injects the token into the child process only.
- Existing DeepSeek provider keys for the user's other models remain in
  `config.toml`; that file is local and user-owned.

### Verified

- 59 unit tests; Python 3.10/3.11/3.12 CI.
- Regression subset `fix_add`, `security_path`, `concurrency_counter`: 3/3,
  $0.004556, 91.6 % provider cache.
- Grok CLI headless through the wrapper: `ok`.
- MCP: 4 tools discovered, handshake OK.
- Router and watchdog healthy; `momo-router` still configured and listening.

## v0.7.0 — 2026-10-20

- MCP stdio server (`search_code`, `project_map`, `memory_search`, `memory_add`).
- Three benchmark passes over 20 tasks: 60/60, p50 15.9 s, p95 21.4 s.
- Real Click regression task fixed; 37/37 targeted tests, 2179 total.
- Security review and operations guide.
