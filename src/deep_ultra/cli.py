"""User-operated CLI. No automatic file edits or unsafe terminal commands."""
from pathlib import Path
import typer
from .core import Cache, MODELS, ask, run_tests, safe_repo_files
app = typer.Typer(help='Deep Ultra: context-aware programming assistant')


def db() -> Cache:
    return Cache(Path.home() / '.cache' / 'deep-ultra' / 'state.sqlite3')


@app.command()
def models():
    for m in MODELS:
        typer.echo(f'{m.name}: {m.provider} vision={m.vision} reasoning={m.reasoning}; key={m.key_var}')


@app.command()
def chat(prompt: str, model: str = typer.Option('', '--model'), image: Path | None = typer.Option(None, '--image'),
         repo: Path | None = typer.Option(None, '--repo'), ttl: int = typer.Option(3600, '--ttl', min=0)):
    try:
        output, chosen, cached = ask(prompt, db(), vision_path=image, override=model or None, root=repo, ttl=ttl)
        typer.echo(f'[{chosen.name} | {"cache" if cached else "API"}]\n{output}')
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
