"""User-operated CLI. No automatic file edits or unsafe terminal commands."""
import dataclasses
import datetime as dt
import json
import os
from pathlib import Path

import httpx
import typer

from . import finops
from .catalog import META, MODELS, ModelSpec
from .core import DEFAULT_MAX_TOKENS, Cache, DeferredWork, ask, run_tests, safe_repo_files
from .finops import Budget, BudgetExceeded, PricingUnavailable

app = typer.Typer(help='Deep Ultra: context-aware programming assistant')
memory_app = typer.Typer(help='Local programming memory')
token_app = typer.Typer(help='Router token in the OS credential store')
app.add_typer(memory_app, name='memory')
app.add_typer(token_app, name='token')


def db() -> Cache:
    return Cache(Path.home() / '.cache' / 'deep-ultra' / 'state.sqlite3')


def _limit(value: int | None) -> int:
    if value:
        return value
    return int(os.getenv('DEEP_ULTRA_MAX_TOKENS', DEFAULT_MAX_TOKENS))


def _price(model: ModelSpec) -> str:
    peak = model.price('peak')
    off = model.price('off_peak')
    if not peak or not off:
        return 'price unverified'
    return (f"peak ${peak.input:.3f}/${peak.output:.3f} | "
            f"off-peak ${off.input:.3f}/${off.output:.3f} per 1M")


@app.command()
def models():
    typer.echo(f"catalog checked {META['checked_at']}")
    for m in MODELS:
        state = 'set' if m.available() else 'missing'
        typer.echo(f"{m.id}: {m.provider} tier={m.tier} ctx={m.context} "
                   f"vision={m.vision} tools={m.tools} cache={m.cache} | {_price(m)} | {m.key_var}={state}")


@app.command()
def chat(prompt: str, model: str = typer.Option('', '--model'), image: Path | None = typer.Option(None, '--image'),
         repo: Path | None = typer.Option(None, '--repo'), ttl: int = typer.Option(3600, '--ttl', min=0),
         max_tokens: int | None = typer.Option(None, '--max-tokens', min=1), task: str = typer.Option('default', '--task'),
         budget: float | None = typer.Option(None, '--budget-usd', min=0), defer: bool = typer.Option(False, '--defer'),
         max_wait: int = typer.Option(0, '--max-wait', min=0, help='Seconds a deferred call may wait')):
    config = Budget.from_env()
    if budget is not None:
        config = dataclasses.replace(config, task_usd=budget)
    cache = db()
    try:
        answer = ask(prompt, cache, vision_path=image, override=model or None, root=repo, ttl=ttl,
                     max_tokens=_limit(max_tokens), task=task, budget=config,
                     defer=defer, max_wait=max_wait)
    except DeferredWork as exc:
        typer.echo(f'Deferred: {exc}', err=True)
        raise typer.Exit(2)
    except (BudgetExceeded, PricingUnavailable) as exc:
        typer.echo(f'Blocked: {exc}', err=True)
        raise typer.Exit(3)
    except Exception as exc:
        typer.echo(f'Error: {exc}', err=True)
        raise typer.Exit(1)
    usage = answer.usage
    if answer.cached:
        detail = f'cache hit | saved ${answer.saved_real_usd:.6f}'
    else:
        detail = (f"in={usage.get('prompt_tokens', '?')} out={usage.get('completion_tokens', '?')} "
                  f"| ${answer.cost_usd:.6f} | est. saved ${answer.saved_est_usd:.6f}")
    typer.echo(f"[{answer.model.id} | {answer.tariff} | {detail}]\n{answer.text}")
    for alert in cache.ledger.alerts(config, dt.datetime.now(dt.timezone.utc), task):
        typer.echo(f'Alert: {alert}', err=True)


@app.command()
def analyze(repo: Path = typer.Argument(Path('.')), as_json: bool = typer.Option(False, '--json'),
            budget_tokens: int = typer.Option(6000, '--budget-tokens', min=500),
            changed: bool = typer.Option(False, '--changed')):
    """Summarise a project into a bounded context pack (no API calls)."""
    from .context import ProjectIndex, build_pack, render_markdown
    pack = build_pack(repo, budget_tokens)
    typer.echo(json.dumps(pack, indent=2, ensure_ascii=False) if as_json else render_markdown(pack))
    if changed:
        index = ProjectIndex(Path.home() / '.cache' / 'deep-ultra' / 'index.sqlite3')
        diff = index.refresh(repo)
        typer.echo(f"index: +{len(diff['added'])} ~{len(diff['changed'])} "
                   f"-{len(diff['removed'])} total={diff['total']}")


@app.command()
def context(query: str, repo: Path = typer.Argument(Path('.')),
            budget_tokens: int = typer.Option(1500, '--budget-tokens', min=200)):
    """Search the incremental code index and print the best snippets."""
    from .retrieval import CodeIndex
    index = CodeIndex(Path.home() / '.cache' / 'deep-ultra' / 'code.sqlite3')
    stats = index.refresh(repo)
    text = index.snippets(repo, query, budget_tokens)
    typer.echo(text or 'No matches')
    typer.echo(f"# indexed={stats['indexed']} changed={stats['changed']}", err=True)


@memory_app.command('add')
def memory_add(kind: str, key: str, value: str, ttl_days: int = typer.Option(90, '--ttl-days', min=1)):
    from .memory import Memory
    Memory(Path.home() / '.cache' / 'deep-ultra' / 'memory.sqlite3').add(kind, key, value, ttl_days)
    typer.echo('Saved')


@memory_app.command('search')
def memory_search(query: str, limit: int = typer.Option(5, '--limit', min=1)):
    from .memory import Memory
    for item in Memory(Path.home() / '.cache' / 'deep-ultra' / 'memory.sqlite3').search(query, limit):
        typer.echo(f"[{item['kind']}] {item['key']}: {item['value']}")


@memory_app.command('list')
def memory_list():
    from .memory import Memory
    for item in Memory(Path.home() / '.cache' / 'deep-ultra' / 'memory.sqlite3').list():
        typer.echo(f"{item['id']} [{item['kind']}] {item['key']}: {item['value']}")


@memory_app.command('prune')
def memory_prune():
    from .memory import Memory
    typer.echo(f"removed={Memory(Path.home() / '.cache' / 'deep-ultra' / 'memory.sqlite3').prune()}")


@token_app.command('show')
def token_show():
    """Print the token for a launcher. Never log its output."""
    from .token import get
    value = get()
    if not value:
        typer.echo('No router token stored', err=True)
        raise typer.Exit(1)
    typer.echo(value)


@token_app.command('rotate')
def token_rotate():
    from .token import rotate
    token = rotate()
    typer.echo(f'rotated ({len(token)} hex chars)')


@token_app.command('set')
def token_set():
    """Read a token from stdin so it never appears in the process list."""
    import sys
    from .token import store
    value = sys.stdin.readline().strip()
    if not value:
        typer.echo('Empty token', err=True)
        raise typer.Exit(1)
    store(value)
    typer.echo(f'stored ({len(value)} chars)')


@token_app.command('status')
def token_status():
    from .token import get
    typer.echo('stored' if get() else 'missing')


@token_app.command('delete')
def token_delete():
    from .token import delete
    delete()
    typer.echo('deleted')


@app.command()
def files(repo: Path = Path('.')):
    for name in safe_repo_files(repo):
        typer.echo(name)


@app.command()
def remember(key: str, value: str):
    db().remember(key, value)
    typer.echo('Saved locally')


@app.command()
def recall(key: str):
    typer.echo(db().recall(key) or 'Not found')


@app.command('test')
def test_command(repo: Path = Path('.')):
    result = run_tests(repo.resolve())
    typer.echo(result.stdout)
    typer.echo(result.stderr, err=True)
    raise typer.Exit(result.returncode)


@app.command()
def budget():
    config = Budget.from_env()
    ledger = db().ledger
    now = dt.datetime.now(dt.timezone.utc)
    today = ledger.spent_since(now.replace(hour=0, minute=0, second=0, microsecond=0))
    typer.echo(f"tariff now: {finops.tariff(now)}")
    typer.echo(f"daily spend: ${today:.6f} / ${config.daily_usd:.6f}")
    typer.echo(f"task budget: ${config.task_usd:.6f} | calls/task cap: {config.max_calls_per_task}")
    for alert in ledger.alerts(config, now):
        typer.echo(f'Alert: {alert}')


@app.command()
def usage(days: int = typer.Option(30, '--days', min=1)):
    report = db().ledger.report(days)
    typer.echo(f"events={report['events']} api_calls={report['events'] - report['cache_hits']} "
               f"cache_hits={report['cache_hits']}")
    typer.echo(f"tokens in={report['prompt_tokens']} cached={report['cached_tokens']} "
               f"out={report['completion_tokens']}")
    typer.echo(f"spent=${report['cost_usd']:.6f} | real saved=${report['saved_real_usd']:.6f} "
               f"| estimated saved=${report['saved_est_usd']:.6f}")
    for row in report['by_model']:
        typer.echo(f"  {row['model']}: events={row['events']} cost=${row['cost_usd']:.6f}")


@app.command()
def refresh():
    """Compare the packaged catalog with each provider's live /v1/models (no billing)."""
    drift = False
    for provider in sorted({m.provider for m in MODELS}):
        sample = next(m for m in MODELS if m.provider == provider)
        token = os.getenv(sample.key_var)
        if not token:
            typer.echo(f'{provider}: {sample.key_var} missing, skipped')
            continue
        try:
            response = httpx.get(sample.endpoint.rstrip('/') + '/models',
                                 headers={'Authorization': f'Bearer {token}'}, timeout=20)
            response.raise_for_status()
            live = {item['id'] for item in response.json().get('data', [])}
        except Exception as exc:  # network or auth failure
            typer.echo(f'{provider}: refresh failed ({exc})', err=True)
            drift = True
            continue
        known = {m.id for m in MODELS if m.provider == provider}
        typer.echo(f"{provider}: live={sorted(live)} catalog={sorted(known)}")
        missing = live - known
        stale = known - live
        if missing or stale:
            drift = True
            typer.echo(f'  new: {sorted(missing)} | gone: {sorted(stale)}', err=True)
    raise typer.Exit(1 if drift else 0)


@app.command()
def serve(port: int = typer.Option(6011, '--port', min=1)):
    """Run the local OpenAI-compatible router for Grok CLI."""
    from .proxy import serve as run
    run(port)


@app.command()
def mcp(repo: Path = typer.Option(Path('.'), '--repo')):
    """Serve retrieval, project analysis and memory over MCP stdio."""
    from .mcp import serve
    serve(repo)


@app.command()
def bench(tasks: Path = Path('benchmarks/tasks'), max_usd: float = typer.Option(0.10, '--max-usd', min=0),
          grok: str = typer.Option('grok', '--grok'), out: Path | None = typer.Option(None, '--out'),
          only: str = typer.Option('', '--only', help='Comma-separated task ids')):
    """Run verifiable coding tasks through Grok CLI and report real results."""
    from .bench import run_bench
    selected = {name.strip() for name in only.split(',') if name.strip()} or None
    summary = run_bench(tasks, grok=grok, max_usd=max_usd, out=out, only=selected)
    typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))


@app.command()
def validate():
    """Offline integrity checks for the catalog and budget defaults (no network)."""
    problems = []
    ids = [m.id for m in MODELS]
    if len(ids) != len(set(ids)):
        problems.append('duplicate model ids')
    for m in MODELS:
        if m.context <= 0:
            problems.append(f'{m.id}: invalid context')
        if m.max_output and m.max_output > m.context:
            problems.append(f'{m.id}: max_output exceeds context')
        if m.pricing_verified:
            peak, off = m.price('peak'), m.price('off_peak')
            if not peak or not off:
                problems.append(f'{m.id}: missing peak/off-peak price')
            elif off.input > peak.input or off.output > peak.output or off.cache_hit > peak.cache_hit:
                problems.append(f'{m.id}: off-peak is not cheaper than peak')
    config = Budget.from_env()
    if not 0 <= config.alert_pct <= 1:
        problems.append('alert_pct must be between 0 and 1')
    for problem in problems:
        typer.echo(f'FAIL {problem}', err=True)
    typer.echo('catalog ok' if not problems else f'{len(problems)} problem(s)')
    raise typer.Exit(1 if problems else 0)


if __name__ == '__main__':
    app()
