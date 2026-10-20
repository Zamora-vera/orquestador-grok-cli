"""Dated model catalog with explicit provenance.

Nothing here is treated as permanent: every entry carries the source URL and the
date it was checked, and prices that were not read from an official page stay
``None`` so the router refuses to spend against them.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name('catalog.json')
DEFAULT_CHECKED_AT = '2026-10-20'


@dataclass(frozen=True)
class Price:
    cache_hit: float
    input: float
    output: float


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider: str
    version: str
    context: int
    max_output: int
    vision: bool
    tools: bool
    reasoning: bool
    cache: str
    tier: int
    endpoint: str
    key_var: str
    pricing: dict[str, Price] | None
    pricing_verified: bool
    pricing_source: str
    checked_at: str = DEFAULT_CHECKED_AT

    def price(self, tariff: str) -> Price | None:
        if not self.pricing:
            return None
        return self.pricing.get(tariff)

    def available(self) -> bool:
        return bool(os.getenv(self.key_var))


def _load(path: Path) -> tuple[tuple[ModelSpec, ...], dict[str, Any]]:
    raw = json.loads(path.read_text(encoding='utf-8'))
    models = []
    for item in raw['models']:
        pricing = None
        if item.get('pricing'):
            pricing = {name: Price(**values) for name, values in item['pricing'].items()}
        models.append(ModelSpec(
            id=item['id'], provider=item['provider'], version=item.get('version', item['id']),
            context=int(item['context']), max_output=int(item.get('max_output', 0)),
            vision=bool(item['vision']), tools=bool(item['tools']), reasoning=bool(item['reasoning']),
            cache=item.get('cache', 'none'), tier=int(item.get('tier', 1)),
            endpoint=item['endpoint'], key_var=item['key_var'], pricing=pricing,
            pricing_verified=bool(item.get('pricing_verified', False)),
            pricing_source=item.get('pricing_source', ''),
            checked_at=raw.get('checked_at', DEFAULT_CHECKED_AT),
        ))
    meta = {'checked_at': raw.get('checked_at', DEFAULT_CHECKED_AT), 'sources': raw.get('sources', {})}
    return tuple(models), meta


def load_catalog(path: Path | None = None) -> tuple[tuple[ModelSpec, ...], dict[str, Any]]:
    """Load the packaged catalog, or DEEP_ULTRA_CATALOG when set."""
    override = os.getenv('DEEP_ULTRA_CATALOG')
    chosen = path or (Path(override) if override else CATALOG_PATH)
    return _load(chosen)


MODELS, META = load_catalog()


def get(model_id: str, models: tuple[ModelSpec, ...] = MODELS) -> ModelSpec | None:
    return next((m for m in models if m.id == model_id), None)


def with_pricing(models: tuple[ModelSpec, ...] = MODELS) -> list[ModelSpec]:
    return [m for m in models if m.pricing_verified and m.pricing]
