import importlib.util
from pathlib import Path

from vision.voice import VoiceService


def helper():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'prepare_voice_announcements.py'
    spec = importlib.util.spec_from_file_location('prepare_voice_announcements', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_only_never_calls_cloud_or_creates_cache(tmp_path, monkeypatch):
    script = helper()
    service = VoiceService(tmp_path, transport=lambda *args: (_ for _ in ()).throw(AssertionError('No cloud call')))
    monkeypatch.setattr(service, 'settings', lambda: ('', 'marin'))
    messages = []
    result = script.prepare(service, report=messages.append)
    assert result == {'total': 26, 'cached': 0, 'generated': 0, 'failed': 0}
    assert len(set(script.ANNOUNCEMENTS)) == 26
    assert not list(tmp_path.iterdir())
    assert 'Report only' in messages[-1]


def test_explicit_generation_is_sequential_bounded_and_reuses_service_cache(tmp_path, monkeypatch):
    script = helper()
    calls, messages = [], []
    now = [0.]
    def transport(path, body, headers, limit):
        calls.append((now[0], path))
        return {'Content-Type': 'audio/mpeg'}, b'ID3mock-announcement'
    service = VoiceService(tmp_path, transport=transport)
    monkeypatch.setattr(service, 'settings', lambda: ('sk-test-only-not-a-real-credential', 'cedar'))
    def sleep(seconds):
        now[0] += seconds
    result = script.prepare(service, generate=True, report=messages.append, clock=lambda: now[0], sleep=sleep)
    assert result == {'total': 26, 'cached': 0, 'generated': 26, 'failed': 0}
    assert all(second[0] - first[0] >= 2.099 for first, second in zip(calls, calls[1:]))
    assert all(path == 'audio/speech' for _, path in calls)
    assert len(list((tmp_path / 'voice-cache').glob('*.mp3'))) == 26
    assert not any(text in '\n'.join(messages) for text in script.ANNOUNCEMENTS)
    assert 'sk-test' not in '\n'.join(messages)
    # The running service can use these exact cache entries without more API work.
    service.observe(script.ANNOUNCEMENTS)
    for text in script.ANNOUNCEMENTS:
        assert service.speech({'text': text}, 'test-client') == b'ID3mock-announcement'
    assert len(calls) == 26
    assert script.prepare(service, generate=True, report=messages.append)['generated'] == 0
    assert len(calls) == 26


def test_generation_stops_after_first_failure_without_transcripts_or_credentials(tmp_path, monkeypatch):
    script = helper()
    calls, messages = [], []
    def transport(*args):
        calls.append(args)
        raise RuntimeError('sk-private-do-not-echo')
    service = VoiceService(tmp_path, transport=transport)
    monkeypatch.setattr(service, 'settings', lambda: ('sk-private-do-not-echo', 'marin'))
    result = script.prepare(service, generate=True, report=messages.append)
    assert result['failed'] == 1 and result['generated'] == 0 and len(calls) == 1
    assert 'sk-private' not in '\n'.join(messages)
    assert not list(tmp_path.glob('voice-cache/*.mp3'))
