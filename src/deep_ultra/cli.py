"""User-operated CLI. No automatic file edits or unsafe terminal commands."""
import dataclasses
import datetime as dt
import os
from pathlib import Path

import httpx
import typer

from . import finops
from .catalog import META, MODELS, ModelSpec
from .core import DEFAULT_MAX_TOKENS, Cache, DeferredWork, ask, run_tests, safe_repo_files
from .finops import Budget, BudgetExceeded, PricingUnavailable

app = typer.Typer(help='Deep Ultra: context-aware programming assistant')


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
