# Operations guide

Install, configure, upgrade, recover and remove Deep Ultra. All paths are per
user; no administrator rights are required except for system-wide tools.

## Install

```powershell
git clone https://github.com/Zamora-vera/orquestador-grok-cli.git
cd orquestador-grok-cli
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
pytest -q
```

Set the router token and start the service:

```powershell
$du = "$HOME\.grok\deep-ultra"
New-Item -ItemType Directory -Force $du | Out-Null
# 40+ random characters
Set-Content "$du\router-token.txt" -Value ([guid]::NewGuid().ToString('N')) -NoNewline
& "$du\start-router.ps1"
& "$du\status-router.ps1"
```

The scheduled task **Deep Ultra Router** starts the router at logon; **Deep Ultra
Watchdog** checks health every five minutes.

## Configure Grok CLI

Add the model block (keep `[models] default` untouched) and the MCP server:

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

[mcp_servers.deep-ultra]
command = "<repo>\.venv\Scripts\python.exe"
args = ["-m", "deep_ultra.cli", "mcp", "--repo", "."]
startup_timeout_sec = 20
```

Verify: `grok mcp doctor deep-ultra` and `grok -p "ok" -m deep-ultra`.

## Budgets

Set before starting the router:

| Variable | Effect |
|---|---|
| `DEEP_ULTRA_TASK_BUDGET_USD` | Per-task ceiling |
| `DEEP_ULTRA_DAILY_BUDGET_USD` | UTC-day ceiling |
| `DEEP_ULTRA_ALERT_PCT` | Alert threshold (default 0.8) |
| `DEEP_ULTRA_PROXY_MAX_CALLS` | Calls per task (default 200) |
| `DEEP_ULTRA_PROXY_MAX_OUTPUT` | Output cap per call (default 4096) |

`deep-ultra usage --days N` reports spend, tokens, cache and savings.

## Upgrade

```powershell
git pull
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
pytest -q
deep-ultra validate
& "$HOME\.grok\deep-ultra\stop-router.ps1"
& "$HOME\.grok\deep-ultra\start-router.ps1"
```

Schema migrations run on startup (cache, ledger, indexes). Keep the config
backups; do not delete them during an upgrade.

## Recover

- **Router down:** `& "$HOME\.grok\deep-ultra\start-router.ps1"`; if it fails,
  check `router.log` and `router.err.log` in the same folder.
- **Bad config:** restore the newest `config.toml.backup-*` and restart the CLI.
- **Spend alarm:** lower `DEEP_ULTRA_DAILY_BUDGET_USD`, restart the router, then
  `deep-ultra usage --days 1` to identify the task.
- **Stale memory:** `deep-ultra memory prune` and remove entries with
  `deep-ultra memory list` followed by a new `memory add` if needed.
- **Corrupt index:** delete `~/.cache/deep-ultra/code.sqlite3` and
  `~/.cache/deep-ultra/index.sqlite3`; they rebuild on the next query.

## Uninstall

```powershell
& "$HOME\.grok\deep-ultra\stop-router.ps1"
Unregister-ScheduledTask -TaskName 'Deep Ultra Router' -Confirm:$false
Unregister-ScheduledTask -TaskName 'Deep Ultra Watchdog' -Confirm:$false
# Remove [model.deep-ultra] and [mcp_servers.deep-ultra] from config.toml,
# or restore config.toml.backup-*
Remove-Item "$HOME\.grok\deep-ultra" -Recurse -Force
Remove-Item "$HOME\.cache\deep-ultra" -Recurse -Force
```

The repository folder can then be deleted. Removing the cache deletes memory,
the ledger and the code index; export `deep-ultra usage` first if the spend
history matters.
