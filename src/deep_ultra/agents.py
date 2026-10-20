"""Specialist prompt profiles for focused coding work."""
from __future__ import annotations

from .context import build_pack, render_markdown

PROFILES: dict[str, dict] = {
    'architect': {
        'system': ('You are a senior software architect. Map the project structure, boundaries, '
                   'dependencies and risks. Propose the smallest design that solves the task. '
                   'Cite files from the provided context; do not invent paths.'),
        'keywords': ('architecture', 'arquitectura', 'design', 'diseño', 'module', 'módulo',
                     'structure', 'estructura', 'dependency', 'dependencia'),
    },
    'coder': {
        'system': ('You are a precise senior programmer. Produce minimal, correct diffs and '
                   'explain the exact files and tests they affect. Never claim work is done '
                   'without a test result.'),
        'keywords': ('implement', 'implementa', 'feature', 'funcionalidad', 'add', 'agrega',
                     'refactor', 'code', 'código'),
    },
    'debugger': {
        'system': ('You are a debugging specialist. Reproduce the failure, isolate the root '
                   'cause, state the hypothesis, then propose the smallest fix and the command '
                   'that proves it.'),
        'keywords': ('bug', 'error', 'falla', 'failing', 'debug', 'traceback', 'exception',
                     'regression', 'regresión', 'fix', 'arregla'),
    },
    'security': {
        'system': ('You are an application security reviewer. Look for injection, auth gaps, '
                   'secret leaks, unsafe deserialization, path traversal and dependency risk. '
                   'Rank findings by exploitability and give a concrete remediation for each.'),
        'keywords': ('security', 'seguridad', 'vulnerab', 'auth', 'login', 'secret', 'token',
                     'injection', 'inyección', 'permission', 'permiso'),
    },
    'tester': {
        'system': ('You are a test engineer. Identify untested behavior, propose high-signal '
                   'tests, and name the exact command to run them. Prefer few meaningful tests '
                   'over broad shallow coverage.'),
        'keywords': ('test', 'tests', 'prueba', 'pruebas', 'coverage', 'cobertura', 'pytest',
                     'unit', 'integration', 'integración'),
    },
}

DEFAULT_PROFILE = 'coder'


def select_profile(task: str) -> str:
    """Pick a specialist by keyword score; ties fall back to the coder profile."""
    text = task.lower()
    scores = {name: sum(1 for keyword in spec['keywords'] if keyword in text)
              for name, spec in PROFILES.items()}
    best = max(scores, key=lambda name: (scores[name], name == DEFAULT_PROFILE))
    return best if scores[best] else DEFAULT_PROFILE


def agent_prompt(task: str, pack: dict) -> str:
    return (f'Task:\n{task}\n\n'
            f'Project context (bounded, generated locally):\n{render_markdown(pack)}\n\n'
            'Return: findings, the exact files to touch, the patch or commands, and how to verify.')


def run_agent(profile: str, task: str, root, *, budget_tokens: int = 6000,
              cache=None, ask_func=None, max_tokens: int | None = None):
    """Build the pack and call the shared ask() path with the specialist prompt."""
    from .core import ask

    chosen = select_profile(task) if profile == 'auto' else profile
    if chosen not in PROFILES:
        raise ValueError(f'Unknown profile: {profile}')
    pack = build_pack(root, budget_tokens)
    prompt = agent_prompt(task, pack)
    answer = (ask_func or ask)(prompt, cache, root=None, max_tokens=max_tokens,
                              system=PROFILES[chosen]['system'], task=f'agent:{chosen}',
                              route_text=task)
    return chosen, pack, answer
