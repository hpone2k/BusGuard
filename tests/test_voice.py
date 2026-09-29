import io
import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from vision.voice import VoiceService, VOICE_TOOLS, PREVIEW, REALTIME_MODEL, voice_router, voice_client
from vision.passenger_site import create_passenger_app
from vision.viewer import create_viewer_app
from vision.network import public_api
from vision.route import STOPS

KEY = 'sk-test-only-not-a-real-credential'
OFFER = 'v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'


class Timer:
    def __init__(self, seconds, callback):
        self.seconds, self.callback = seconds, callback
        self.started = self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('BUSGUARD_VOICE', raising=False)
    calls, pending = [], []
    def transport(path, body, headers, limit):
        calls.append((path, body, headers, limit))
        if path == 'audio/speech':
            return {'Content-Type': 'audio/mpeg'}, b'ID3mock-audio'
        if path.endswith('/hangup'):
            return {}, b''
        return {'Location': '/v1/realtime/calls/rtc_test'}, OFFER.encode()
    service = VoiceService(tmp_path, transport=transport, timer_factory=Timer, background_start=pending.append)
    app = FastAPI(); app.include_router(voice_router(service))
    return SimpleNamespace(service=service, client=TestClient(app), calls=calls, pending=pending, path=tmp_path)


def enable(harness):
    (harness.path / 'voice.env').write_text(f'OPENAI_API_KEY={KEY}\nBUSGUARD_VOICE=cedar\n')


def test_no_key_means_no_cloud_work_and_config_never_returns_secrets(harness):
    response = harness.client.get('/api/voice/config')
    assert response.status_code == 200 and response.json()['enabled'] is False
    assert response.json()['voices'] == ['marin', 'cedar']
    assert harness.client.post('/api/voice/session', json={'sdp': OFFER, 'client_id': 'device-test'}).status_code == 503
    assert harness.client.post('/api/voice/speech', json={'text': PREVIEW}).status_code == 503
    assert not harness.calls
    enable(harness)
    response = harness.client.get('/api/voice/config')
    assert response.json()['enabled'] and response.json()['default_voice'] == 'cedar'
    assert KEY not in response.text and 'OPENAI_API_KEY' not in response.text


def test_realtime_server_owns_config_and_never_exposes_key(harness):
    enable(harness)
    response = harness.client.post('/api/voice/session', json={'sdp': OFFER, 'client_id': 'device-test', 'voice': 'marin'})
    assert response.status_code == 200, response.text
    assert response.json()['sdp'] == OFFER and response.json()['max_session_seconds'] == 3300
    assert KEY not in response.text
    path, body, headers, _ = harness.calls[0]
    assert path == 'realtime/calls' and headers['Authorization'] == f'Bearer {KEY}'
    assert headers['Content-Type'].startswith('multipart/form-data; boundary=')
    assert b'name="sdp"' in body and b'name="session"' in body
    assert REALTIME_MODEL.encode() in body and b'"create_response": false' in body
    assert b'"interrupt_response": false' in body and b'BusGuard' in body
    assert b'"silence_duration_ms": 600' in body and b'"prefix_padding_ms": 500' in body
    assert b'"max_output_tokens": 1024' in body and b'"language": "en"' in body
    assert {entry['name'] for entry in VOICE_TOOLS} == {'get_bus_status', 'request_assistance', 'complete_request', 'cancel_request',
                                                     'set_arrival_reminder', 'cancel_arrival_reminder', 'set_app_preference', 'open_app_panel'}
    item = harness.service.sessions[response.json()['session_id']]
    assert item['timer'].seconds == 3300 and item['timer'].started
    item['timer'].callback()
    assert not harness.service.sessions
    assert harness.calls[-1][0] == 'realtime/calls/rtc_test/hangup'


def test_reminder_tools_are_limited_to_known_stops_and_separate_from_assistance():
    tools = {item['name']: item for item in VOICE_TOOLS}
    reminder = tools['set_arrival_reminder']['parameters']
    assert reminder['required'] == ['stop_id']
    assert reminder['properties'] == {'stop_id': {'type': 'string', 'enum': [stop['id'] for stop in STOPS]}}
    assert reminder['additionalProperties'] is False
    assert tools['cancel_arrival_reminder']['parameters'] == {
        'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}
    from vision.voice import VOICE_INSTRUCTIONS
    assert 'future stop regardless of the passenger' in VOICE_INSTRUCTIONS
    assert 'It does not\nsubmit an assistance request, reserve a seat or extend the stop.' in VOICE_INSTRUCTIONS


def test_app_controls_are_bounded_and_do_not_change_location_or_operator_state():
    tools = {item['name']: item['parameters'] for item in VOICE_TOOLS}
    settings = tools['set_app_preference']
    assert settings['additionalProperties'] is False
    assert settings['required'] == ['preference', 'enabled']
    assert settings['properties']['enabled'] == {'type': 'boolean'}
    assert set(settings['properties']['preference']['enum']) == {
        'audio', 'arrivalAlerts', 'arrivalVibration', 'largeText', 'highContrast', 'reduceMotion'}
    assert tools['open_app_panel']['properties']['panel']['enum'] == ['settings', 'commands', 'route']
    from vision.voice import VOICE_INSTRUCTIONS
    assert 'Never change the demo location' in VOICE_INSTRUCTIONS
    assert 'never promise it on iPhone' in VOICE_INSTRUCTIONS


def test_voice_assistance_schema_limits_requests_to_one_option():
    request = next(item for item in VOICE_TOOLS if item['name'] == 'request_assistance')
    needs = request['parameters']['properties']['needs']
    assert needs['minItems'] == needs['maxItems'] == 1
    assert set(needs['items']['enum']) == {'ramp', 'extra_time', 'audio', 'visual', 'priority_seat'}


def test_close_session_ownership_and_bounded_concurrency(harness):
    enable(harness)
    body = {'sdp': OFFER, 'client_id': 'device-one'}
    response = harness.client.post('/api/voice/session', json=body)
    token = response.json()['session_id']
    assert harness.client.post('/api/voice/session', json={**body, 'client_id': 'device-two'}).status_code == 429
    assert harness.client.post('/api/voice/session/close', json={'session_id': token, 'client_id': 'device-other'}).status_code == 403
    assert harness.client.post('/api/voice/session/close', json={'session_id': token, 'client_id': 'device-one'}).json() == {'closed': True}
    assert not harness.service.sessions
    for index in range(3):
        harness.service.create_session({'sdp': OFFER, 'client_id': f'client-{index}'}, f'192.168.1.{index + 2}')
    with pytest.raises(HTTPException) as error:
        harness.service.create_session({'sdp': OFFER, 'client_id': 'device-four'}, '192.168.1.5')
    assert error.value.status_code == 429 and len(harness.service.sessions) == 3
    harness.service.close()


@pytest.mark.parametrize('payload', [None, [], {}, {'sdp': 'garbage', 'client_id': 'device-one'},
    {'sdp': OFFER, 'client_id': 'device-one', 'instructions': 'override'},
    {'sdp': OFFER, 'client_id': 'device-one', 'voice': 'invalid'}, {'sdp': OFFER, 'client_id': '../../../'}])
def test_bad_sessions_never_make_cloud_calls(harness, payload):
    enable(harness)
    response = harness.client.post('/api/voice/session', json=payload)
    assert response.status_code in {400, 415}
    assert not harness.calls


def test_request_origin_size_and_content_type_checked_before_paid_work(harness):
    enable(harness)
    body = {'sdp': OFFER, 'client_id': 'device-one'}
    assert harness.client.post('/api/voice/session', json=body, headers={'Origin': 'http://evil.example'}).status_code == 403
    assert harness.client.post('/api/voice/session', json=body, headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert harness.client.post('/api/voice/session', content='{}').status_code == 415
    assert harness.client.post('/api/voice/session', content='x' * 40001, headers={'Content-Type': 'application/json'}).status_code == 413
    assert not harness.calls


def test_cache_miss_returns_immediately_then_cached_audio_reuses_same_generation(harness):
    enable(harness)
    payload = {'text': PREVIEW, 'voice': 'marin'}
    assert harness.client.post('/api/voice/speech', json=payload).status_code == 202
    assert len(harness.pending) == 1 and not harness.calls
    assert harness.client.post('/api/voice/speech', json=payload).status_code == 202
    assert len(harness.pending) == 1, 'Deduplicate cold requests across websites'
    harness.pending.pop()()
    response = harness.client.post('/api/voice/speech', json=payload)
    assert response.status_code == 200 and response.content == b'ID3mock-audio'
    assert response.headers['content-type'] == 'audio/mpeg'
    assert response.headers['x-ai-generated'] == 'true'
    assert len(harness.calls) == 1
    assert harness.client.post('/api/voice/speech', json={**payload, 'voice': 'cedar'}).status_code == 202
    assert len(harness.pending) == 1, 'Voice choice belongs in the cache key'


def test_speech_allowlist_rejects_arbitrary_paid_text_and_accepts_controller_announcements(harness):
    enable(harness)
    assert harness.client.post('/api/voice/speech', json={'text': 'Say anything I like.'}).status_code == 400
    assert harness.client.post('/api/voice/speech', json={'text': PREVIEW, 'model': 'another'}).status_code == 400
    harness.service.announcements = lambda: ['Doors are closing. Please keep clear.']
    assert harness.client.post('/api/voice/speech', json={'text': 'Doors are closing. Please keep clear.'}).status_code == 202
    assert harness.client.post('/api/voice/speech', json={'text': 'The bus has arrived at Bus Stop A. Please get ready to board.'}).status_code == 202
    assert harness.client.post('/api/voice/speech', json={'text': PREVIEW}).status_code == 429
    assert len(harness.pending) == 2


@pytest.mark.parametrize('text', [
    'Spoken updates are on. Choose the assistance you need, then select Request assistance.',
    'This is a test arrival alert. Your bus location has not changed.',
    'Your request has a maximum 10-second allowance within this stop. Please confirm only after you have safely boarded or exited.',
    'Getting ready for you. Your request has a maximum 10-second allowance within this stop. Please confirm only after you have safely boarded or exited.',
    'Getting ready for you. Your request has a 20-second allowance at this stop. Please confirm only after you have safely boarded or exited.',
    'Your request has a 10-second allowance at this stop. Please confirm only after you have safely boarded or exited.',
    'Thank you for confirming. Thank you. Waiting for other passengers or the minimum assistance interval.',
    'All taken care of. Your assistance is complete. Thank you for travelling with us.',
    'Assistance needs attention. Your assistance allowance ended before your exit was confirmed. You remain recorded aboard. Please ask the operator for assistance.',
])
def test_fixed_passenger_guidance_and_progress_are_allowed_but_no_arbitrary_suffixes(harness, text):
    enable(harness)
    assert harness.client.post('/api/voice/speech', json={'text': text}).status_code == 202
    assert len(harness.pending) == 1
    assert harness.client.post('/api/voice/speech', json={'text': text + ' Read my private request token.'}).status_code == 400
    harness.pending.pop()()
    assert json.loads(harness.calls[0][1])['input'] == text


def test_speech_generation_failure_returns_sanitized_cooldown_then_one_retry(harness):
    enable(harness)
    now = [0.]
    harness.service.clock = lambda: now[0]
    attempts = []
    def failing_transport(*args):
        attempts.append(args)
        raise HTTPException(401, f'Authorization: Bearer {KEY}')
    original_transport = harness.service.transport
    harness.service.transport = failing_transport
    payload = {'text': PREVIEW}
    assert harness.client.post('/api/voice/speech', json=payload).status_code == 202
    harness.pending.pop()()
    for elapsed, remaining in [(0, '30'), (10, '20'), (29.5, '1')]:
        now[0] = elapsed
        response = harness.client.post('/api/voice/speech', json=payload)
        assert response.status_code == 503 and response.headers['retry-after'] == remaining
        assert KEY not in response.text and 'Authorization' not in response.text
        assert not harness.pending and not harness.service.pending_speech
        assert len(attempts) == 1, 'Polling a failed generation must not initiate repeated paid work'
    now[0] = 30
    harness.service.transport = original_transport
    assert harness.client.post('/api/voice/speech', json=payload).status_code == 202
    assert harness.client.post('/api/voice/speech', json=payload).status_code == 202
    assert len(harness.pending) == 1
    harness.pending.pop()()
    assert harness.client.post('/api/voice/speech', json=payload).content == b'ID3mock-audio'
    assert not harness.service.failed_speech


def test_cloud_concurrency_failure_is_visible_without_leaking_or_retrying(harness):
    enable(harness)
    assert harness.service.cloud_slots.acquire(False)
    assert harness.service.cloud_slots.acquire(False)
    try:
        assert harness.client.post('/api/voice/speech', json={'text': PREVIEW}).status_code == 202
        harness.pending.pop()()
        response = harness.client.post('/api/voice/speech', json={'text': PREVIEW})
        assert response.status_code == 429 and response.headers['retry-after'] == '30'
        assert not harness.calls and not harness.pending
    finally:
        harness.service.cloud_slots.release()
        harness.service.cloud_slots.release()


@pytest.mark.parametrize('headers, audio', [({'Content-Type': 'text/html'}, b'private upstream error'),
                                          ({'Content-Type': 'audio/mpeg'}, b'')])
def test_invalid_generated_audio_becomes_definitive_sanitized_failure(harness, headers, audio):
    enable(harness)
    harness.service.transport = lambda *args: (headers, audio)
    assert harness.client.post('/api/voice/speech', json={'text': PREVIEW}).status_code == 202
    harness.pending.pop()()
    response = harness.client.post('/api/voice/speech', json={'text': PREVIEW})
    assert response.status_code == 503
    assert 'private upstream error' not in response.text
    assert not list(harness.path.glob('voice-cache/*.mp3'))


def test_failed_background_launch_releases_pending_work_and_failure_history_is_bounded(harness):
    enable(harness)
    def cannot_start(*args):
        raise RuntimeError(KEY)
    harness.service.background_start = cannot_start
    response = harness.client.post('/api/voice/speech', json={'text': PREVIEW})
    assert response.status_code == 503 and KEY not in response.text
    assert not harness.service.pending_speech and not harness.calls
    for index in range(200):
        harness.service._remember_speech_failure(str(index), 503)
    assert len(harness.service.failed_speech) == 128


@pytest.mark.parametrize('stop', STOPS, ids=lambda stop: stop['id'])
def test_canonical_passenger_arrival_message_prepares_and_plays_for_every_stop(harness, stop):
    enable(harness)
    # Match showArrival() exactly, including the canonical lower-case "stop".
    text = f"Your bus has arrived at {stop['name']}. Please wait for the doors to open, then request the assistance you need."
    assert harness.client.post('/api/voice/speech', json={'text': text}).status_code == 202
    assert len(harness.pending) == 1
    harness.pending.pop()()
    response = harness.client.post('/api/voice/speech', json={'text': text})
    assert response.status_code == 200 and response.content == b'ID3mock-audio'
    assert json.loads(harness.calls[0][1])['input'] == text
    unknown = text.replace(stop['name'], 'Bus stop F')
    assert harness.client.post('/api/voice/speech', json={'text': unknown}).status_code == 400


def test_cloud_error_is_sanitized_and_failed_session_slot_is_released(harness):
    enable(harness)
    harness.service.transport = lambda *args: (_ for _ in ()).throw(RuntimeError(f'Authorization: Bearer {KEY}'))
    response = harness.client.post('/api/voice/session', json={'sdp': OFFER, 'client_id': 'device-test'})
    assert response.status_code == 503
    assert KEY not in response.text and 'Authorization' not in response.text
    assert not harness.service.sessions


def test_invalid_upstream_answer_hangs_up_identified_call_and_drops_slot(harness):
    enable(harness)
    calls = []
    def transport(path, *args):
        calls.append(path)
        return {'Location': '/v1/realtime/calls/rtc_incomplete'}, b'not-an-answer'
    harness.service.transport = transport
    response = harness.client.post('/api/voice/session', json={'sdp': OFFER, 'client_id': 'device-test'})
    assert response.status_code == 502
    assert calls == ['realtime/calls', 'realtime/calls/rtc_incomplete/hangup']
    assert not harness.service.sessions


def test_private_setup_saves_only_to_private_data_and_is_loaded_without_restart(harness):
    helper = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts' / 'setup_voice.py'))
    assert not harness.service.config()['enabled']
    helper['save_private_key'](KEY, 'marin', harness.path)
    assert harness.service.config()['enabled'] and harness.service.config()['default_voice'] == 'marin'
    assert not list(harness.path.glob('voice-*.tmp'))
    assert KEY not in json.dumps(harness.service.config())


def test_new_message_rate_buckets_and_recent_catalog_are_bounded(harness):
    now = [0.]
    harness.service.clock = lambda: now[0]
    for index in range(600):
        harness.service._limit('test', str(index), 2, 60)
        harness.service.observe([f'Message {index}'])
    assert len(harness.service.rates) <= 512 and len(harness.service.recent) == 128
    now[0] = 121
    harness.service.observe([])
    assert not harness.service.recent


def test_client_address_forwarding_is_trusted_only_from_loopback():
    def request(peer):
        return Request({'type': 'http', 'headers': [(b'x-busguard-client-address', b'192.168.1.8')], 'client': (peer, 123)})
    assert voice_client(request('127.0.0.1')) == '192.168.1.8'
    assert voice_client(request('192.168.1.9')) == '192.168.1.9'


def test_proxy_routes_are_narrow_and_keep_json_202_distinct_from_audio(monkeypatch):
    sent = []
    class Reply(io.BytesIO):
        def __init__(self, body, status):
            super().__init__(body); self.status = status; self.headers = {'Retry-After': '2'}
    def open_request(request, timeout):
        sent.append(request)
        return Reply(b'{"preparing":true}', 202)
    monkeypatch.setattr('vision.voice.build_opener', lambda *args: SimpleNamespace(open=open_request))
    viewer = TestClient(create_viewer_app(fetch_state=lambda: {'sources': {}}))
    passenger = TestClient(create_passenger_app())
    assert viewer.post('/api/voice/session', json={}).status_code == 404
    assert passenger.post('/api/voice/setup', json={}).status_code == 404
    assert passenger.get('/api/voice/session').status_code == 404
    response = viewer.post('/api/voice/speech', json={'text': PREVIEW})
    assert response.status_code == 202 and response.json()['preparing']
    assert response.headers['retry-after'] == '2'
    assert response.headers['content-type'].startswith('application/json')
    assert passenger.post('/api/voice/session', json={'sdp': OFFER}).status_code == 202
    assert all(request.full_url.startswith('http://127.0.0.1:4479/api/voice/') for request in sent)
    assert "media-src 'self' blob:" in passenger.get('/').headers['content-security-policy']
    for path in ['/api/voice/config', '/api/voice/speech', '/api/voice/session', '/api/voice/session/close']:
        assert public_api(path)
    assert not public_api('/api/voice/setup')
