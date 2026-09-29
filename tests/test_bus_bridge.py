import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from vision.api import create_app
from vision.bus_bridge import VisionBusBridge, assistance_type
from vision.config import Settings
from vision.schema import Detection, Options, RFIDRequest, SessionRequest
from vision.scheduler import SupersededError
from vision.sessions import Session
from test_api import encoded_image, wait_until


class Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def wall(self):
        return 1_800_000_000 + self.now

    def advance(self, seconds=.05):
        self.now += seconds


def source(bridge, role='outside', kind='live'):
    session = Session(Options(), kind, source_role=role, source_name=role.title() + ' CCTV')
    bridge.register(session)
    return session


def publish(bridge, session, label='wheelchair', track_id=5, predicted=False, received=None, demo=False):
    frame_id = session.latest + 1
    session.accept(frame_id)
    detections = [] if label is None else [Detection(label, [.1, .1, .5, .8], .88,
                                                     track_id=track_id, predicted=predicted).json()]
    return bridge.publish(session, {'frame_id': frame_id, 'detections': detections,
                                   'inference_ms': 27}, received, {'demo': demo})


@pytest.fixture
def setup():
    clock = Clock()
    return VisionBusBridge(clock.monotonic, clock.wall), clock


def test_outside_observed_confirmation_and_inside_unassigned_isolation(setup):
    bridge, clock = setup
    outside = source(bridge)
    inside = source(bridge, 'inside')
    unassigned = source(bridge, 'unassigned')
    for session in (inside, unassigned):
        publish(bridge, session); publish(bridge, session)
    assert not bridge.snapshot()['events']
    publish(bridge, outside)
    publish(bridge, outside, predicted=True)
    assert not bridge.snapshot()['events']
    publish(bridge, outside)
    assert not bridge.snapshot()['events']
    publish(bridge, outside)
    state = bridge.snapshot()
    event = state['events'][0]
    assert event['type'] == 'wheelchair' and event['track_id'] == 5
    assert event['source_kind'] == 'live' and event['source_role'] == 'outside'
    assert event['confidence'] == .88 and event['session_id'] == outside.id
    assert state['sources']['inside']['connected']
    for _ in range(15):
        publish(bridge, outside)
    assert len(bridge.snapshot()['events']) == 1


def test_freshness_uses_server_receipt_and_empty_frames_clear_state(setup):
    bridge, clock = setup
    session = source(bridge)
    publish(bridge, session)
    assert bridge.snapshot()['sources']['outside']['detections']
    publish(bridge, session, label=None)
    assert bridge.snapshot()['sources']['outside']['detections'] == []
    assert not bridge.snapshot()['events']
    receipt = bridge.receipt()
    clock.advance(2.6)
    publish(bridge, session, received=receipt)
    state = bridge.snapshot()
    assert state['sources']['outside']['status'] == 'stale'
    assert state['sources']['outside']['detections'] == [] and not state['events']
    publish(bridge, session)
    clock.advance(2.6)
    assert not bridge.snapshot()['sources']['outside']['connected']
    publish(bridge, session)
    assert not bridge.snapshot()['events']  # Must confirm again after the freshness gap.


def test_source_reassignment_and_disconnect_do_not_resurrect_old_frames(setup):
    bridge, _ = setup
    old = source(bridge)
    publish(bridge, old)
    new = source(bridge)
    assert bridge.snapshot()['sources']['outside']['status'] == 'waiting'
    assert publish(bridge, old) is False
    assert bridge.snapshot()['sources']['outside']['session_id'] == new.id
    publish(bridge, new)
    bridge.disconnect(old.id)
    assert bridge.snapshot()['sources']['outside']['connected']
    bridge.disconnect(new.id)
    assert bridge.snapshot()['sources']['outside']['status'] == 'disconnected'
    assert bridge.snapshot()['sources']['outside']['detections'] == []
    assert publish(bridge, new) is False
    assert bridge.source_list()['sources'] == []


def test_image_provenance_and_demo_backend_cannot_generate_live_assistance(setup):
    bridge, clock = setup
    image = source(bridge, kind='image')
    publish(bridge, image, label='stroller', track_id=None)
    publish(bridge, image, label='stroller', track_id=None)
    state = bridge.snapshot()
    assert len(state['events']) == 1
    assert state['events'][0]['type'] == 'pram' and state['events'][0]['source_kind'] == 'image'
    clock.advance(10.01)
    assert bridge.snapshot()['sources']['outside']['detections'] == []
    live = source(bridge)
    publish(bridge, live, demo=True); publish(bridge, live, demo=True)
    assert len(bridge.snapshot()['events']) == 1
    assert bridge.snapshot()['sources']['outside']['is_demo'] is True


def test_untracked_presence_is_debounced_without_repeating_continuous_alerts(setup):
    bridge, clock = setup
    session = source(bridge)
    for _ in range(50):
        publish(bridge, session, label='walking stick', track_id=None)
        clock.advance(.5)
    assert len(bridge.snapshot()['events']) == 1
    publish(bridge, session, label=None)
    clock.advance(3.1)
    publish(bridge, session, label='cane', track_id=None)
    clock.advance()
    publish(bridge, session, label='cane', track_id=None)
    assert len(bridge.snapshot()['events']) == 2
    replacement = source(bridge)
    publish(bridge, replacement, label='cane', track_id=None)
    publish(bridge, replacement, label='cane', track_id=None)
    assert len(bridge.snapshot()['events']) == 2  # Restart/settings changes cannot flood alerts.


def test_aliases_validation_bounded_rfid_history_and_snapshot_independence(setup):
    bridge, _ = setup
    for label in ('person', 'senior', 'old person', 'pregnant woman', 'no wheelchair', 'fat person'):
        assert assistance_type(label) is None
    assert assistance_type('Walking-frame') == 'walking'
    assert assistance_type('PERSON IN A WHEELCHAIR') == 'wheelchair'
    for body in ({'source_role': 'roof'}, {'source_name': ' '}, {'source_name': 'x' * 81}):
        with pytest.raises(ValidationError):
            SessionRequest(**body)
    with pytest.raises(ValidationError):
        RFIDRequest(passenger_type='pregnant', event_id='tap-1')
    first = bridge.rfid(RFIDRequest(passenger_type='senior', event_id='tap-1'))
    repeat = bridge.rfid(RFIDRequest(passenger_type='senior', event_id='tap-1'))
    assert repeat['duplicate'] and first['event']['id'] == repeat['event']['id']
    with pytest.raises(ValueError):
        bridge.rfid(RFIDRequest(passenger_type='assistance', event_id='tap-1'))
    for index in range(40):
        bridge.rfid(RFIDRequest(passenger_type='assistance', event_id=f'tap-{index+2}'))
    snapshot = bridge.snapshot()
    assert len(snapshot['instance_id']) == 32
    assert snapshot['instance_id'] == bridge.snapshot()['instance_id']
    assert snapshot['instance_id'] != VisionBusBridge().snapshot()['instance_id']
    assert len(snapshot['events']) == 30
    assert snapshot['events'][-1]['id'] == 41
    assert snapshot['events'][-1]['origin'] == 'rfid'
    snapshot['events'][-1]['type'] = 'modified'
    assert bridge.snapshot()['events'][-1]['type'] == 'assistance'


class SemanticBackend:
    info = {'name': 'Test detector', 'model': 'test', 'device': 'cpu',
            'phrases': True, 'scores': True, 'demo': False}

    def warmup(self):
        pass

    def detect(self, frame, options):
        return [Detection('wheelchair', [.1, .1, .8, .8], .9)]


def test_api_publishes_actual_session_output_and_rfid_validation(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), backend_factory=SemanticBackend)
    app.state.assistance.vehicle['doors'] = 'open'  # Admit outside/unassigned test frames.
    with TestClient(app) as client:
        wait_until(lambda: client.get('/api/status').json(), lambda data: data['state'] == 'ready')
        created = client.post('/api/sessions', json={'kind': 'live', 'source_role': 'outside',
            'source_name': 'Boarding camera', 'options': {'prompt': 'wheelchair', 'stabilization': 'off'}})
        assert created.status_code == 201
        session_id = created.json()['id']
        for frame_id in (1, 2):
            result = client.post(f'/api/sessions/{session_id}/frames?frame_id={frame_id}&captured_at={frame_id * 100}',
                                 content=encoded_image())
            assert result.status_code == 200, result.text
        state = client.get('/api/bus/state').json()
        assert state['sources']['outside']['detections'] == result.json()['detections']
        assert state['events'][0]['type'] == 'wheelchair'
        assert state['sources']['outside']['source_name'] == 'Boarding camera'
        assert client.get('/api/bus/sources').json()['sources'][0]['source_role'] == 'outside'
        assert client.post(f'/api/sessions/{session_id}/frames?frame_id=1&captured_at=100',
                           content=encoded_image()).status_code == 409
        assert len(client.get('/api/bus/state').json()['events']) == 1
        client.delete(f'/api/sessions/{session_id}')
        assert not client.get('/api/bus/state').json()['sources']['outside']['connected']
        assert client.post('/api/bus/rfid', json={'passenger_type': 'senior', 'event_id': 'reader.123'}).status_code == 200
        assert client.post('/api/bus/rfid', json={'passenger_type': 'senior', 'event_id': 'reader.123'}).json()['duplicate']
        assert client.post('/api/bus/rfid', json={'passenger_type': 'senior', 'event_id': ''}).status_code == 422
        assert client.post('/api/bus/rfid', json={'passenger_type': 'senior', 'event_id': 'x'},
                           headers={'Origin': 'https://other.example'}).status_code == 403


def test_canceled_inflight_frame_never_reaches_bus_state(tmp_path):
    class SlowBackend(SemanticBackend):
        entered = threading.Event()
        release = threading.Event()

        def detect(self, frame, options):
            self.entered.set()
            self.release.wait(5)
            return super().detect(frame, options)

    backend = SlowBackend()
    app = create_app(Settings(data_dir=tmp_path), backend_factory=lambda: backend)
    app.state.assistance.vehicle['doors'] = 'open'  # Admit outside/unassigned test frames.
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        wait_until(lambda: client.get('/api/status').json(), lambda data: data['state'] == 'ready')
        session_id = client.post('/api/sessions', json={'source_role': 'outside'}).json()['id']
        future = pool.submit(client.post, f'/api/sessions/{session_id}/frames?frame_id=1&captured_at=1',
                             content=encoded_image())
        try:
            assert backend.entered.wait(2)
            client.delete(f'/api/sessions/{session_id}')
        finally:
            backend.release.set()
        assert future.result(timeout=5).status_code == 409
        state = client.get('/api/bus/state').json()
        assert state['events'] == []
        assert state['sources']['outside']['detections'] == []
