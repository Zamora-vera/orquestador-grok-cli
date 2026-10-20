# Deep Ultra runbook

Local OpenAI-compatible router for Grok CLI, bound to `127.0.0.1:6011` with a
bearer token. Kimi models stay disabled until their prices are verified.

## Start, stop, status

```powershell
$du = "$HOME\.grok\deep-ultra"

# Start on demand (uses the scheduled task when present)
& "$du\start-router.ps1"

# Check health (sends the bearer token)
& "$du\status-router.ps1"

# Stop the process listening on 6011
& "$du\stop-router.ps1"
```

Autostart: the scheduled task **Deep Ultra Router** runs `run-router.cmd` at
logon. The task reads the token from `router-token.txt` in the same folder; no
secret is stored in git. A second task, **Deep Ultra Watchdog**, checks health
every five minutes and restarts the router if it stopped responding.

## Enable in Grok CLI

Add this table to `~/.grok/config.toml` (keep `[models] default` untouched):

```toml
[model.deep-ultra]
model = "deep-ultra"
base_url = "http://127.0.0.1:6011/v1"
name = "Deep Ultra"
api_key = "<contents of router-token.txt>"
api_backend = "chat_completions"
vision = true
context_window = 1000000
max_completion_tokens = 4096
temperature = 0
use_concise = true
```

Select it explicitly:

```powershell
grok -p "tu prompt" -m deep-ultra
```

## Disable

Remove the `[model.deep-ultra]` block from `config.toml` and stop the router:

```powershell
& "$HOME\.grok\deep-ultra\stop-router.ps1"
```

Full rollback to the pre-activation configuration:

```powershell
Copy-Item "$HOME\.grok\config.toml.backup-<timestamp>" "$HOME\.grok\config.toml" -Force
```

## Limits and budgets

Environment variables read by the router process:

| Variable | Meaning |
|---|---|
| `DEEP_ULTRA_TASK_BUDGET_USD` | Per-task ceiling; blocks before exceeding |
| `DEEP_ULTRA_DAILY_BUDGET_USD` | UTC-day ceiling |
| `DEEP_ULTRA_ALERT_PCT` | Alert threshold (default 0.8) |
| `DEEP_ULTRA_PROXY_MAX_CALLS` | Calls per task (default 200) |
| `DEEP_ULTRA_PROXY_MAX_OUTPUT` | Output cap per call (default 4096) |
| `DEEP_ULTRA_ROUTER_TOKEN` | Bearer token (set by the launcher) |

`deep-ultra usage --days N` shows spend, cache hits, real savings and estimated
savings. `deep-ultra budget` shows the current tariff and daily spend.

## Verification

```powershell
pytest -q
deep-ultra validate
deep-ultra refresh          # non-billable /v1/models comparison
grok -p "Responde solo: ok" -m deep-ultra --max-turns 1
```
