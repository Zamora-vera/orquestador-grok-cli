import json

from deep_ultra import proxy


def test_text_of_and_vision_detection():
    assert proxy._text_of('hola') == 'hola'
    assert proxy._text_of([{'type': 'text', 'text': 'a'}, {'type': 'image_url'}]) == 'a'
    assert proxy._has_image([{'role': 'user', 'content': [{'type': 'image_url'}]}])
    assert not proxy._has_image([{'role': 'user', 'content': 'hola'}])


def test_request_key_ignores_selected_model():
    messages = [{'role': 'user', 'content': 'hi'}]
    a = proxy._request_key(messages, None, 0, 4096)
    b = proxy._request_key(messages, None, 0, 4096)
    assert a == b
    assert a != proxy._request_key(messages, None, 0, 512)


def test_context_tokens_strips_base64():
    blob = 'data:image/png;base64,' + 'A' * 100_000
    messages = [{'role': 'user', 'content': [{'type': 'text', 'text': 'describe'},
                                             {'type': 'image_url', 'image_url': {'url': blob}}]}]
    assert proxy._context_tokens(messages) < 2000


def test_public_model_names():
    assert proxy._public_name({'model': 'deep-ultra'}) == 'deep-ultra'
    assert proxy._public_name({'model': 'deep-ultra-pro'}) == 'deep-ultra-pro'
    assert proxy._public_name({'model': 'gpt-4'}) == 'deep-ultra'
    assert proxy.FORCED['deep-ultra-pro'] == 'deepseek-v4-pro'


def test_stream_accumulates_tool_calls():
    state = {'content': [], 'reasoning': [], 'tools': {}, 'finish': None}
    proxy._new_stream_message({'role': 'assistant'}, state)
    proxy._new_stream_message({'content': 'hola '}, state)
    proxy._new_stream_message({'tool_calls': [{'index': 0, 'id': 'call_1', 'type': 'function',
                                               'function': {'name': 'list_', 'arguments': '{"pa'}}]}, state)
    proxy._new_stream_message({'tool_calls': [{'index': 0,
                                               'function': {'name': 'dir', 'arguments': 'th":"."}'}}]}, state)
    canonical = proxy._canonical(state, 'deepseek-flash', {'prompt_tokens': 1})
    message = canonical['choices'][0]['message']
    assert message['content'] == 'hola '
    assert message['tool_calls'][0]['function'] == {'name': 'list_dir', 'arguments': '{"path":"."}'}
    assert canonical['model'] == 'deepseek-flash'
    assert json.dumps(canonical)  # serializable


def test_siblings_stay_in_provider(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test')
    monkeypatch.setenv('KIMI_API_KEY', 'test')
    selected = next(m for m in proxy.MODELS if m.id == 'deepseek-v4-pro')
    names = [m.id for m in proxy._siblings(selected, False, 1000)]
    assert names[0] == 'deepseek-v4-pro'
    assert 'deepseek-flash' in names
    assert all(not name.startswith('kimi') for name in names)
    vision = [m.id for m in proxy._siblings(selected, True, 1000)]
    assert 'deepseek-v4-pro' not in vision
    assert 'deepseek-flash' in vision

def test_thinking_policy():
    flash = next(m for m in proxy.MODELS if m.id == 'deepseek-flash')
    pro = next(m for m in proxy.MODELS if m.id == 'deepseek-v4-pro')
    payload = {}
    proxy._apply_thinking(payload, flash, None)
    assert payload['thinking'] == {'type': 'disabled'}
    payload = {}
    proxy._apply_thinking(payload, pro, None)
    assert payload['thinking'] == {'type': 'enabled'}
    assert payload['reasoning_effort'] == 'high'
    payload = {}
    proxy._apply_thinking(payload, flash, 'none')
    assert payload['thinking'] == {'type': 'disabled'}
    payload = {}
    proxy._apply_thinking(payload, flash, 'low')
    assert payload['thinking'] == {'type': 'enabled'}
    assert payload['reasoning_effort'] == 'low'

def test_failure_signal_detects_pytest_output():
    messages = [{'role': 'user', 'content': 'arregla esto'},
                {'role': 'tool', 'content': 'FAILED test_app.py::test_ok - AssertionError'}]
    assert proxy._failure_signal(messages)
    assert not proxy._failure_signal([{'role': 'user', 'content': 'todo bien'}])
