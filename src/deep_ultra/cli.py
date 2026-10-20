"""User-operated CLI. No automatic file edits or unsafe terminal commands."""
import os
from pathlib import Path

import typer

from .core import DEFAULT_MAX_TOKENS, MODELS, Cache, ask, run_tests, safe_repo_files

app = typer.Typer(help='Deep Ultra: context-aware programming assistant')


def db() -> Cache:
    return Cache(Path.home() / '.cache' / 'deep-ultra' / 'state.sqlite3')


def _limit(value: int | None) -> int:
    if value:
        return value
    return int(os.getenv('DEEP_ULTRA_MAX_TOKENS', DEFAULT_MAX_TOKENS))


@app.command()
def models():
    for m in MODELS:
        state = 'set' if os.getenv(m.key_var) else 'missing'
        typer.echo(f'{m.name}: {m.provider} vision={m.vision} reasoning={m.reasoning} '
                   f'cost_tier={m.cost_tier}; {m.key_var}={state}')


@app.command()
def chat(prompt: str, model: str = typer.Option('', '--model'), image: Path | None = typer.Option(None, '--image'),
         repo: Path | None = typer.Option(None, '--repo'), ttl: int = typer.Option(3600, '--ttl', min=0),
         max_tokens: int = typer.Option(None, '--max-tokens', min=1)):
    try:
        output, chosen, cached, usage = ask(prompt, db(), vision_path=image, override=model or None,
                                            root=repo, ttl=ttl, max_tokens=_limit(max_tokens))
        if cached:
            label = 'cache'
        else:
            label = f"API in={usage.get('prompt_tokens', '?')} out={usage.get('completion_tokens', '?')}"
        typer.echo(f'[{chosen.name} | {label}]\n{output}')
    except Exception as exc:
        typer.echo(f'Error: {exc}', err=True)
        raise typer.Exit(1)


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


if __name__ == '__main__':
    app()
