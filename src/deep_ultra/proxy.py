"""OpenAI-compatible router for Grok CLI.

Grok CLI points ``base_url`` here and gets DeepSeek Flash/Pro behind the FinOps
engine: routing, budgets, cache, retries and usage accounting. Streaming, tool
calls and image parts are forwarded to the provider.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

from . import finops
from .catalog import MODELS
from .core import BUDGET_MARGIN, Cache, DEFAULT_MAX_TOKENS, choose_model
from .finops import Budget, PricingUnavailable

PUBLIC = ('deep-ultra', 'deep-ultra-flash', 'deep-ultra-pro')
FORCED = {'deep-ultra-flash': 'deepseek-flash', 'deep-ultra-pro': 'deepseek-v4-pro'}
TIMEOUT = httpx.Timeout(300.0, connect=10.0)
CAP = int(os.getenv('DEEP_ULTRA_PROXY_MAX_OUTPUT', str(DEFAULT_MAX_TOKENS)))
TTL = int(os.getenv('DEEP_ULTRA_PROXY_CACHE_TTL', '3600'))


class UpstreamError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _cache_dir() -> Path:
    return Path(os.getenv('DEEP_ULTRA_CACHE_DIR', str(Path.home() / '.cache' / 'deep-ultra')))


def _log(record: dict) -> None:
    path = _cache_dir() / 'proxy.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {'ts': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), **record}
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, separators=(',', ':')) + '\n')


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ' '.join(part['text'] for part in content
                        if isinstance(part, dict) and part.get('text'))
    return ''


def _has_image(messages: list[dict]) -> bool:
    for message in messages:
        content = message.get('content')
        if isinstance(content, list) and any(
                isinstance(part, dict) and part.get('type') == 'image_url' for part in content):
            return True
    return False


def _prompt_text(messages: list[dict]) -> str:
    """Last user turn without the CLI's rule/user-info blocks."""
    last = next((m for m in reversed(messages) if m.get('role') == 'user'), None)
    text = _text_of(last.get('content')) if last else 'hello'
    text = re.sub(r'<rules>.*?</rules>', ' ', text, flags=re.S)
    text = re.sub(r'<user_info>.*?</user_info>', ' ', text, flags=re.S)
    return (text.strip() or 'hello')[:2000]


def _task_key(messages: list[dict]) -> str:
    system = ' '.join(_text_of(m.get('content')) for m in messages if m.get('role') == 'system')
    return 'proxy:' + hashlib.sha256(system.encode('utf-8')).hexdigest()[:12]


def _request_key(messages: list[dict], tools: Any, temperature: float, max_tokens: int) -> str:
    raw = json.dumps({'m': messages, 't': tools, 'temp': temperature, 'max': max_tokens},
                     sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _context_tokens(messages: list[dict]) -> int:
    raw = json.dumps(messages, ensure_ascii=False)
    stripped = re.sub(r'data:image/[^"]+', '[image]', raw)
    images = sum(1 for m in messages if isinstance(m.get('content'), list))
    return finops.estimate_tokens(stripped) + 1500 * images


def _public_name(body: dict) -> str:
    requested = body.get('model') or 'deep-ultra'
    return requested if requested in PUBLIC else 'deep-ultra'


def _budget() -> Budget:
    base = Budget.from_env()
    return Budget(task_usd=base.task_usd, daily_usd=base.daily_usd, alert_pct=base.alert_pct,
                  max_calls_per_task=int(os.getenv('DEEP_ULTRA_PROXY_MAX_CALLS', '200')),
                  max_repeats=int(os.getenv('DEEP_ULTRA_PROXY_MAX_REPEATS', '10')))


def _siblings(selected, vision: bool, context: int):
    same = [m for m in MODELS if m.provider == selected.provider and m.id != selected.id
            and m.available() and m.pricing_verified and (not vision or m.vision) and m.context >= context]
    first = [selected] if (not vision or selected.vision) else []
    return first + sorted(same, key=lambda m: m.tier)


def _apply_thinking(payload: dict, model, requested: str | None) -> None:
    """Thinking is on by default upstream; keep it off on Flash unless asked."""
    mapping = {'none': None, 'minimal': 'low', 'low': 'low', 'medium': 'high',
               'high': 'high', 'xhigh': 'max', 'max': 'max'}
    if requested:
        effort = mapping.get(requested.lower(), 'high')
        payload['thinking'] = {'type': 'disabled' if effort is None else 'enabled'}
        if effort:
            payload['reasoning_effort'] = effort
        return
    if model.tier >= 2:
        payload['thinking'] = {'type': 'enabled'}
        payload['reasoning_effort'] = 'high'
    else:
        payload['thinking'] = {'type': 'disabled'}


def _new_stream_message(delta: dict, state: dict) -> None:
    if delta.get('content'):
        state['content'].append(delta['content'])
    if delta.get('reasoning_content'):
        state['reasoning'].append(delta['reasoning_content'])
    for call in delta.get('tool_calls') or []:
        index = call.get('index', 0)
        slot = state['tools'].setdefault(index, {'id': '', 'type': 'function',
                                                 'function': {'name': '', 'arguments': ''}})
        if call.get('id'):
            slot['id'] = call['id']
        if call.get('type'):
            slot['type'] = call['type']
        function = call.get('function') or {}
        if function.get('name'):
            slot['function']['name'] += function['name']
        if function.get('arguments'):
            slot['function']['arguments'] += function['arguments']


def _canonical(state: dict, model_id: str, usage: dict) -> dict:
    message: dict[str, Any] = {'role': 'assistant', 'content': ''.join(state['content']) or None}
    if state['reasoning']:
        message['reasoning_content'] = ''.join(state['reasoning'])
    if state['tools']:
        message['tool_calls'] = [state['tools'][i] for i in sorted(state['tools'])]
    return {'id': 'chatcmpl-' + uuid.uuid4().hex[:24], 'object': 'chat.completion',
            'created': int(time.time()), 'model': model_id,
            'choices': [{'index': 0, 'message': message, 'finish_reason': state['finish'] or 'stop'}],
            'usage': usage}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'DeepUltra/0.3'

    def log_message(self, fmt: str, *args) -> None:  # keep the console quiet
        return

    # ---------- plumbing ----------
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str, kind: str = 'upstream_error') -> None:
        self._json(status, {'error': {'message': message, 'type': kind}})

    def _start_stream(self) -> None:
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()

    def _chunk(self, data: bytes) -> None:
        try:
            if not data:
                self.wfile.write(b'0\r\n\r\n')
                return
            self.wfile.write(f'{len(data):X}\r\n'.encode('ascii') + data + b'\r\n')
        except (BrokenPipeError, ConnectionResetError):
            return

    def _event(self, payload: dict) -> None:
        self._chunk(('data: ' + json.dumps(payload, separators=(',', ':')) + '\n\n').encode('utf-8'))

    def _done(self) -> None:
        self._chunk(b'data: [DONE]\n\n')
        self._chunk(b'')

    def _authorized(self) -> bool:
        token = os.getenv('DEEP_ULTRA_ROUTER_TOKEN')
        return not token or self.headers.get('Authorization') == f'Bearer {token}'

    # ---------- routes ----------
    def do_GET(self) -> None:
        if not self._authorized():
            self._error(401, 'unauthorized', 'unauthorized')
            return
        if self.path in ('/health', '/v1/health'):
            self._json(200, {'status': 'ok', 'models': list(PUBLIC)})
            return
        if self.path == '/v1/models':
            data = [{'id': name, 'object': 'model', 'created': 0, 'owned_by': 'deep-ultra'}
                    for name in PUBLIC]
            self._json(200, {'object': 'list', 'data': data})
            return
        self._error(404, 'not found', 'not_found')

    def do_POST(self) -> None:
        if not self._authorized():
            self._error(401, 'unauthorized', 'unauthorized')
            return
        if self.path not in ('/v1/chat/completions', '/chat/completions'):
            self._error(404, 'not found', 'not_found')
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            body = json.loads(self.rfile.read(length) or b'{}')
            self._chat(body)
        except PricingUnavailable as exc:
            self._error(400, str(exc), 'pricing_unverified')
        except finops.BudgetExceeded as exc:
            self._error(402, str(exc), 'budget_exceeded')
        except finops.LoopDetected as exc:
            self._error(429, str(exc), 'loop_detected')
        except Exception as exc:  # noqa: BLE001 - surface a JSON error to the client
            _log({'event': 'error', 'error': str(exc)[:300]})
            self._error(502, str(exc)[:500])

    # ---------- chat ----------
    def _chat(self, body: dict) -> None:
        messages = body.get('messages') or []
        if not isinstance(messages, list) or not messages:
            self._error(400, 'messages is required', 'invalid_request')
            return
        if body.get('n') and int(body['n']) > 1:
            self._error(400, 'n>1 is not supported', 'invalid_request')
            return
        stream = bool(body.get('stream'))
        public = _public_name(body)
        forced = FORCED.get(public)
        vision = _has_image(messages)
        prompt = _prompt_text(messages)
        context = _context_tokens(messages)
        at = dt.datetime.now(dt.timezone.utc)
        model = choose_model(prompt, vision, context, forced, MODELS, at)
        max_tokens = min(int(body.get('max_tokens') or CAP), CAP)
        temperature = float(body.get('temperature') or 0)
        tools = body.get('tools')
        key = _request_key(messages, tools, temperature, max_tokens)
        cache: Cache = self.server.cache  # type: ignore[attr-defined]

        entry = cache.get_entry(key)
        if entry:
            text, cost, usage = entry
            cache.ledger.record(task=_task_key(messages), model=model.id, usage={}, cost=0,
                                saved_real=cost, saved_est=0, at=at, cache_hit=True)
            canonical = json.loads(text)
            _log({'event': 'cache_hit', 'model': model.id, 'stream': stream, 'tools': len(tools or [])})
            if stream:
                self._replay_stream(canonical, public, usage, bool((body.get('stream_options') or {}).get('include_usage')))
            else:
                canonical['id'] = 'chatcmpl-' + uuid.uuid4().hex[:24]
                self._json(200, canonical)
            return

        budget = _budget()
        estimate = finops.estimate_cost(model, context, max_tokens, at) * BUDGET_MARGIN
        cache.ledger.check(_task_key(messages), prompt, estimate, budget, at)

        lock = self.server.lock  # type: ignore[attr-defined]
        inflight = self.server.inflight  # type: ignore[attr-defined]
        with lock:
            event = inflight.get(key)
            owner = event is None
            if owner:
                event = threading.Event()
                inflight[key] = event
        if not owner:
            event.wait(timeout=180)
            entry = cache.get_entry(key)
            if entry:
                text, cost, _ = entry
                canonical = json.loads(text)
                if stream:
                    self._replay_stream(canonical, public, canonical.get('usage') or {}, False)
                else:
                    self._json(200, canonical)
                return
        try:
            self._call(body, messages, tools, temperature, max_tokens, model, vision, context,
                       stream, key, cache, at, public)
        finally:
            if owner:
                with lock:
                    inflight.pop(key, None)
                event.set()

    def _call(self, body, messages, tools, temperature, max_tokens, model, vision, context,
              stream, key, cache, at, public: str) -> None:
        task = _task_key(messages)
        candidates = _siblings(model, vision, context)
        last: Exception | None = None
        for candidate in candidates:
            payload = {'model': candidate.id, 'messages': messages, 'temperature': temperature,
                       'max_tokens': max_tokens, 'stream': stream}
            for field in ('stop', 'top_p', 'tools', 'tool_choice', 'parallel_tool_calls', 'response_format'):
                if body.get(field) is not None:
                    payload[field] = body[field]
            _apply_thinking(payload, candidate, body.get('reasoning_effort'))
            if stream:
                payload['stream_options'] = {'include_usage': True}
            for attempt in range(2):
                if attempt:
                    time.sleep(0.5)
                client = httpx.Client(timeout=TIMEOUT)
                try:
                    request = client.build_request(
                        'POST', candidate.endpoint.rstrip('/') + '/chat/completions',
                        headers={'Authorization': f"Bearer {os.getenv(candidate.key_var)}"},
                        json=payload)
                    response = client.send(request, stream=stream)
                except httpx.HTTPError as exc:
                    client.close()
                    last = exc
                    continue
                if response.status_code >= 400:
                    detail = response.read().decode('utf-8', 'replace')[:400]
                    response.close()
                    client.close()
                    last = UpstreamError(response.status_code, detail)
                    if response.status_code != 429 and response.status_code < 500:
                        break
                    continue
                if stream:
                    self._stream(candidate, response, client, key, cache, at, task, public)
                else:
                    data = response.json()
                    response.close()
                    client.close()
                    data['model'] = public
                    usage = data.get('usage') or {}
                    cost, cache_saving, offpeak = finops.actual_cost(candidate, usage, at)
                    cache.put(key, json.dumps(data), TTL, cost_usd=cost, usage=usage)
                    cache.ledger.record(task=task, model=candidate.id, usage=usage,
                                        cost=cost, saved_real=0, saved_est=cache_saving + offpeak,
                                        at=at, cache_hit=False)
                    _log({'event': 'call', 'model': candidate.id, 'stream': False,
                          'prompt': usage.get('prompt_tokens'), 'cached': usage.get('prompt_cache_hit_tokens'),
                          'completion': usage.get('completion_tokens'), 'cost': cost,
                          'fallback': candidate.id != model.id, 'tools': len(tools or [])})
                    self._json(200, data)
                return
        raise last or UpstreamError(502, 'no upstream model available')

    def _stream(self, candidate, response, client, key, cache, at, task: str, public: str) -> None:
        self._start_stream()
        state = {'content': [], 'reasoning': [], 'tools': {}, 'finish': None}
        usage: dict = {}
        started = time.time()
        try:
            for line in response.iter_lines():
                if not line:
                    self._chunk(b'\n')
                    continue
                if line.startswith('data:'):
                    raw = line[5:].strip()
                    if raw and raw != '[DONE]':
                        try:
                            chunk = json.loads(raw)
                        except json.JSONDecodeError:
                            pass
                        else:
                            usage = chunk.get('usage') or usage
                            for choice in chunk.get('choices') or []:
                                _new_stream_message(choice.get('delta') or {}, state)
                                if choice.get('finish_reason'):
                                    state['finish'] = choice['finish_reason']
                            chunk['model'] = public
                            line = 'data: ' + json.dumps(chunk, separators=(',', ':'))
                self._chunk((line + '\n').encode('utf-8'))
        finally:
            response.close()
            client.close()
            self._chunk(b'')
        canonical = _canonical(state, candidate.id, usage)
        if usage:
            cost, cache_saving, offpeak = finops.actual_cost(candidate, usage, at)
            cache.put(key, json.dumps(canonical), TTL, cost_usd=cost, usage=usage)
            cache.ledger.record(task=task, model=candidate.id, usage=usage, cost=cost,
                                saved_real=0, saved_est=cache_saving + offpeak, at=at, cache_hit=False)
            _log({'event': 'call', 'model': candidate.id, 'stream': True,
                  'prompt': usage.get('prompt_tokens'), 'cached': usage.get('prompt_cache_hit_tokens'),
                  'completion': usage.get('completion_tokens'), 'cost': cost,
                  'ms': int((time.time() - started) * 1000)})
        else:
            _log({'event': 'stream_no_usage', 'model': candidate.id})

    def _replay_stream(self, canonical: dict, public: str, usage: dict, include_usage: bool) -> None:
        self._start_stream()
        base = {'id': canonical.get('id'), 'object': 'chat.completion.chunk',
                'created': canonical.get('created', int(time.time())), 'model': public}
        choice = (canonical.get('choices') or [{}])[0]
        message = choice.get('message') or {}
        self._event({**base, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})
        if message.get('content'):
            self._event({**base, 'choices': [{'index': 0, 'delta': {'content': message['content']},
                                              'finish_reason': None}]})
        for index, call in enumerate(message.get('tool_calls') or []):
            self._event({**base, 'choices': [{'index': 0,
                                              'delta': {'tool_calls': [{'index': index, **call}]},
                                              'finish_reason': None}]})
        self._event({**base, 'choices': [{'index': 0, 'delta': {},
                                          'finish_reason': choice.get('finish_reason') or 'stop'}]})
        if include_usage:
            self._event({**base, 'choices': [], 'usage': usage or canonical.get('usage') or {}})
        self._done()


def serve(port: int | None = None) -> None:
    port = port or int(os.getenv('DEEP_ULTRA_ROUTER_PORT', '6011'))
    cache = Cache(_cache_dir() / 'state.sqlite3')
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.cache = cache  # type: ignore[attr-defined]
    server.lock = threading.Lock()  # type: ignore[attr-defined]
    server.inflight = {}  # type: ignore[attr-defined]
    _log({'event': 'start', 'port': port})
    print(f'Deep Ultra router listening on http://127.0.0.1:{port}/v1', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
