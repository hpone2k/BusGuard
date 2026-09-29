import json
import time

import pytest
from fastapi.testclient import TestClient

from vision.api import create_app
from vision.backends import Demo
from vision.config import Settings
from vision.passenger_site import create_passenger_app
from vision.schema import Options


def request_body(key='passenger-1', token='a' * 48, **changes):
    body = dict(client_request_id=key, request_token=token, journey='boarding',
                stop_id='campus', needs=['extra_time']) | changes
    body.setdefault('location', {'mode': 'onboard'} if body['journey'] == 'alighting' else
                    {'mode': 'at_stop', 'stop_id': body['stop_id']})
    return body


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(backend='demo', data_dir=tmp_path), backend_factory=Demo, lan=True)


@pytest.fixture
def remote(app):
    # Do not start inference or heartbeat threads: these tests exercise the shared
    # HTTP/controller boundaries deterministically, without models or hardware.
    return TestClient(app, client=('192.168.20.25', 52000))


def test_remote_camera_and_operator_access_require_pairing(app, remote):
    assert remote.get('/api/status').status_code == 200
    access = remote.get('/api/access').json()
    assert not access['operator'] and access['pairing_code'] is None
    for method, endpoint, body in [
        ('get', '/api/bus/cameras', None),
        ('get', '/api/bus/cameras/inside/preview.jpg', None),
        ('post', '/api/sessions', {}),
        ('post', '/api/bus/rfid', {'event_id': 'tap', 'passenger_type': 'senior'}),
        ('post', '/api/assistance/control', {'action': 'emergency'}),
    ]:
        response = getattr(remote, method)(endpoint, **({'json': body} if body is not None else {}))
        assert response.status_code == 403, response.text
    paired = remote.post('/api/access/pair', json={'code': app.state.access.pin})
    assert paired.status_code == 200
    assert 'HttpOnly' in paired.headers['set-cookie']
    assert 'SameSite=strict' in paired.headers['set-cookie']
    assert remote.get('/api/bus/cameras').status_code == 200
    assert remote.post('/api/assistance/control', json={'action': 'emergency'}).status_code == 200


def test_forwarded_headers_cannot_impersonate_host_operator(remote):
    forged = remote.get('/api/bus/cameras', headers={'X-Forwarded-For': '127.0.0.1',
                                                    'X-Real-IP': '127.0.0.1'})
    assert forged.status_code == 403


def test_passenger_create_retry_ownership_and_shared_bus_state(app, remote):
    first = remote.post('/api/assistance/requests', json=request_body())
    assert first.status_code == 200
    item = first.json()
    assert remote.post('/api/assistance/requests', json=request_body()).json()['id'] == item['id']
    assert remote.post('/api/assistance/requests', json=request_body(token='b' * 48)).status_code == 409
    path = '/api/assistance/requests/' + item['id']
    assert remote.get(path).status_code == 404
    assert remote.post(path + '/cancel', headers={'X-Request-Token': 'b' * 48}).status_code == 404
    own = {'X-Request-Token': 'a' * 48}
    assert remote.get(path, headers=own).json()['id'] == item['id']
    public = remote.get('/api/assistance/state').json()
    bus = remote.get('/api/bus/state').json()['assistance']
    assert public['active_count'] == bus['active_count'] == 1
    assert public['vehicle'] == bus['vehicle']
    for state in (public, bus):
        text = json.dumps(state)
        assert item['id'] not in text and 'a' * 48 not in text and 'passenger-1' not in text
    assert remote.post(path + '/cancel', headers=own).json()['status'] == 'cancelled'


def test_passenger_http_rejects_multiple_options_before_creating_assistance(app, remote):
    response = remote.post('/api/assistance/requests', json=request_body(needs=['ramp', 'visual']))
    assert response.status_code == 400
    assert 'one assistance option' in response.json()['detail']
    assert remote.get('/api/assistance/state').json()['active_count'] == 0
    assert remote.post('/api/assistance/requests', json=request_body(needs=['ramp'])).status_code == 200


def test_passenger_http_enforces_demo_location_and_preserves_old_saved_retries(app, remote):
    missing = request_body()
    del missing['location']
    assert remote.post('/api/assistance/requests', json=missing).status_code == 400
    assert remote.post('/api/assistance/requests', json=request_body(location={'mode': 'away'})).status_code == 409
    assert remote.post('/api/assistance/requests', json=request_body(stop_id='stop_d')).status_code == 409
    malformed = request_body(location={'mode': 'onboard', 'stop_id': 'campus'})
    assert remote.post('/api/assistance/requests', json=malformed).status_code == 422
    assert remote.get('/api/assistance/state').json()['active_count'] == 0
    created = remote.post('/api/assistance/requests', json=request_body()).json()
    # New eligibility does not prevent retrieving the result of an old request.
    app.state.assistance.vehicle['motion'] = 'moving'
    assert remote.post('/api/assistance/requests', json=missing).json()['id'] == created['id']
    assert remote.post('/api/assistance/requests', json=request_body('new')).status_code == 409


def test_route_configuration_and_snapshot_share_all_five_stop_ids(remote):
    config = remote.get('/api/assistance/config').json()
    assert len(config['stops']) == 5
    assert config['route']['stops'] == config['stops']
    assert config['route']['simulated']
    state = remote.get('/api/assistance/state').json()['route']
    assert state['current_stop_id'] == 'campus'
    assert state['next_stop_id'] == 'interchange'
    assert state['phase'] == 'at_stop'


def test_cross_origin_posts_and_unknown_hosts_are_rejected(remote):
    for endpoint, body in [('/api/assistance/requests', request_body()),
                           ('/api/access/pair', {'code': '123456'})]:
        assert remote.post(endpoint, json=body, headers={'Origin': 'https://outside.example'}).status_code == 403
        assert remote.post(endpoint, json=body, headers={'Origin': 'null'}).status_code == 403
    assert remote.get('/api/assistance/state', headers={'Host': 'outside.example'}).status_code == 400
    assert remote.post('/api/assistance/requests', json=request_body(),
                       headers={'Origin': 'http://testserver'}).status_code == 200


def test_request_survives_api_restart_but_cannot_resume_without_operator_reset(app, remote):
    created = remote.post('/api/assistance/requests', json=request_body()).json()
    settings = Settings(backend='demo', data_dir=app.state.assistance.path.parent)
    restored_app = create_app(settings, backend_factory=Demo, lan=True)
    restored = TestClient(restored_app, client=('192.168.20.25', 52000))
    state = restored.get('/api/assistance/state').json()
    assert state['vehicle']['revalidation_required']
    assert not state['readiness']['can_depart']
    path = '/api/assistance/requests/' + created['id']
    owner = {'X-Request-Token': 'a' * 48}
    assert restored.get(path, headers=owner).json()['status'] == 'queued'
    assert restored.post(path + '/complete', headers=owner).status_code == 409
    host = TestClient(restored_app, client=('127.0.0.1', 52001))
    assert host.post('/api/assistance/control', json={'action': 'reset'}).status_code == 200
    assert restored.get(path, headers=owner).json()['status'] == 'assisting'


def test_gate_persists_when_inside_source_removed_and_outside_false_cannot_disable(app, remote):
    sessions, bridge = app.state.sessions, app.state.bus_bridge
    session = sessions.create(Options(prompt='person', posture_enabled=True), 'live', 'inside', 'Cabin')
    bridge.register(session)
    assert remote.get('/api/assistance/state').json()['readiness']['posture_enabled']
    bridge.disconnect(session.id)
    outside = sessions.create(Options(prompt='person', posture_enabled=False), 'live', 'outside', 'Outside')
    bridge.register(outside)
    state = remote.get('/api/assistance/state').json()
    assert state['readiness']['posture_enabled']
    assert not state['readiness']['can_depart']


@pytest.mark.parametrize('kind,is_demo', [('image', False), ('video', False), ('live', True)])
def test_recorded_or_demo_vision_events_never_create_assistance(app, remote, kind, is_demo):
    bridge = app.state.bus_bridge
    # Baseline before inserting a new bridge event.
    remote.get('/api/assistance/state')
    bridge._event(type='wheelchair', origin='vision', source_role='outside', source_kind=kind,
                  timestamp_ms=time.time() * 1000, is_demo=is_demo)
    assert remote.get('/api/assistance/state').json()['active_count'] == 0


def test_live_rfid_event_creates_shared_request_only_once(app, remote):
    host = TestClient(app, client=('127.0.0.1', 52001))
    remote.get('/api/assistance/state')
    body = {'event_id': 'reader-tap-1', 'passenger_type': 'senior'}
    assert host.post('/api/bus/rfid', json=body).status_code == 200
    state = remote.get('/api/assistance/state').json()
    assert state['active_count'] == 1
    assert state['requests'][0]['origin'] == 'rfid'
    assert 'extra_time' in state['requests'][0]['needs']
    assert host.post('/api/bus/rfid', json=body).json()['duplicate']
    assert remote.get('/api/assistance/state').json()['active_count'] == 1


def test_storage_failure_returns_unconfirmed_error_and_holds_bus(app, remote, monkeypatch):
    def broken_save():
        raise OSError('Disk unavailable')
    monkeypatch.setattr(app.state.assistance, '_save', broken_save)
    response = remote.post('/api/assistance/requests', json=request_body())
    assert response.status_code == 503
    assert app.state.assistance.vehicle['emergency']
    assert app.state.assistance.vehicle['motion'] == 'stationary'


def test_passenger_site_proxy_is_allowlisted_and_preserves_ownership(app, monkeypatch):
    main = TestClient(app, client=('127.0.0.1', 53000))
    forwarded = []

    class UpstreamResponse:
        def __init__(self, response):
            self.response, self.status = response, response.status_code
        def read(self, size):
            return self.response.content[:size]
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass

    class Opener:
        def open(self, request, timeout):
            forwarded.append(request)
            path = request.full_url.split('4479', 1)[1]
            return UpstreamResponse(main.request(request.method, path, content=request.data,
                                                headers=dict(request.header_items())))

    monkeypatch.setattr('vision.passenger_site.build_opener', lambda *_: Opener())
    client = TestClient(create_passenger_app(), client=('192.168.20.26', 54000))
    assert client.post('/api/assistance/control', json={'action': 'emergency'}).status_code == 404
    assert client.get('/api/bus/cameras').status_code == 404
    assert not forwarded
    item = client.post('/api/assistance/requests', json=request_body()).json()
    path = '/api/assistance/requests/' + item['id']
    assert client.get(path).status_code == 404
    assert client.get(path, headers={'X-Request-Token': 'a' * 48,
                                     'Cookie': 'bustech_operator=do-not-forward'}).json()['id'] == item['id']
    assert forwarded[-1].get_header('X-request-token') == 'a' * 48
    assert forwarded[-1].get_header('Cookie') is None
    assert client.post('/api/assistance/requests', json=request_body('other'),
                       headers={'Origin': 'https://outside.example'}).status_code == 403
    assert client.post('/api/assistance/requests', content=b'a' * 8193).status_code == 413


def test_passenger_site_offline_does_not_claim_request_was_sent(monkeypatch):
    class Offline:
        def open(self, *_args, **_kwargs):
            raise OSError('Offline')
    monkeypatch.setattr('vision.passenger_site.build_opener', lambda *_: Offline())
    client = TestClient(create_passenger_app())
    response = client.post('/api/assistance/requests', json=request_body())
    assert response.status_code == 503
    assert 'not been confirmed' in response.json()['detail']
