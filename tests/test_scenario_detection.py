"""Journey gating at the HTTP, worker and result-publication boundaries."""
import threading
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from vision.api import create_app
from vision.assistance import AssistanceController
from vision.backends import Demo
from vision.bus_bridge import VisionBusBridge
from vision.config import Settings
from vision.detection_gate import DetectionGate, DetectionPausedError
from vision.schema import Detection, Options
from vision.sessions import Sessions
from timing_helpers import assume_clear_standing_check


class Backend:
    def __init__(self, after_detect=None):
        self.calls = 0
        self.after_detect = after_detect

    def detect(self, frame, options):
        self.calls += 1
        if self.after_detect:
            self.after_detect()
        return []


def setup(tmp_path, kind='live', role='outside'):
    policy = {'enabled': True, 'generation': 'stop-1'}
    gate = DetectionGate(lambda: dict(policy))
    bridge = VisionBusBridge()
    sessions = Sessions(Settings(data_dir=tmp_path), SimpleNamespace(cancel=lambda key: None), bridge, gate)
    session = sessions.create(Options(prompt='person'), kind, role)
    session.accept(1)
    return policy, gate, bridge, sessions, session


@pytest.mark.parametrize('kind', ['live', 'image', 'video'])
def test_travel_prevents_outside_model_work_for_every_source_kind(tmp_path, kind):
    policy, gate, bridge, sessions, session = setup(tmp_path, kind)
    policy['enabled'] = False
    backend = Backend()
    with pytest.raises(DetectionPausedError):
        sessions.infer(session, np.zeros((8, 8, 3), np.uint8), 1, 100, None, backend, threading.Event(), 0)
    assert backend.calls == 0
    assert bridge.snapshot()['sources']['outside']['frame_id'] is None


def test_departure_during_inference_discards_result_before_pose_or_votes(tmp_path):
    policy, gate, bridge, sessions, session = setup(tmp_path)
    backend = Backend(lambda: policy.update(enabled=False, generation='travel-1'))
    with pytest.raises(DetectionPausedError):
        sessions.infer(session, np.zeros((8, 8, 3), np.uint8), 1, 100, None, backend, threading.Event(), 0)
    assert backend.calls == 1
    assert session.count_voter.last_observed_ms is None
    assert bridge.snapshot()['sources']['outside']['frame_id'] is None


def test_old_stop_result_cannot_publish_after_a_new_stop_and_new_votes_start_fresh(tmp_path):
    policy, gate, bridge, sessions, session = setup(tmp_path)
    frame, backend, cancel = np.zeros((8, 8, 3), np.uint8), Backend(), threading.Event()
    result = sessions.infer(session, frame, 1, 100, None, backend, cancel, 0)
    previous_voter = session.count_voter
    policy.update(generation='stop-2')
    with pytest.raises(DetectionPausedError):
        sessions.finalize(session, result, cancel)
    assert previous_voter.last_observed_ms is None
    assert bridge.snapshot()['sources']['outside']['frame_id'] is None
    session.accept(2)
    fresh = sessions.infer(session, frame, 2, 200, None, backend, cancel, 0)
    assert session.count_voter is not previous_voter
    assert sessions.finalize(session, fresh, cancel)
    assert bridge.snapshot()['sources']['outside']['frame_id'] == 2
    assert '_detection_generation' not in fresh


def test_queued_frame_token_is_rejected_before_starting_the_model(tmp_path):
    policy, gate, bridge, sessions, session = setup(tmp_path)
    captured_generation = gate.check()
    policy.update(generation='stop-2')
    backend = Backend()
    with pytest.raises(DetectionPausedError):
        sessions.infer(session, np.zeros((8, 8, 3), np.uint8), 1, 100, None, backend,
                       threading.Event(), 0, captured_generation)
    assert backend.calls == 0


@pytest.mark.parametrize('scenario_enabled', [False, True])
@pytest.mark.parametrize('doors', ['unknown', 'closed', 'opening', 'open', 'closing'])
@pytest.mark.parametrize('motion', ['stationary', 'braking', 'moving'])
def test_only_fully_open_stationary_doors_enable_outside_detection(scenario_enabled, doors, motion):
    controller = AssistanceController()
    controller.scenario.enabled = scenario_enabled
    controller.vehicle.update(doors=doors, motion=motion)
    gate = DetectionGate(controller.detection_policy)
    expected = doors == 'open' and motion == 'stationary'
    assert gate.enabled('outside') is expected
    assert controller.scenario.snapshot()['detection_enabled'] is expected
    assert gate.enabled('inside')
    assert gate.capture('inside')['generation'] == 'inside-live'
    assert gate.capture('inside')['posture_token'][0] is (doors == 'closed')


@pytest.mark.parametrize('boundary', ['queued', 'inference', 'publication'])
@pytest.mark.parametrize('reopen', [False, True])
def test_closing_invalidates_outside_frames_even_if_doors_reopen(tmp_path, boundary, reopen):
    controller = AssistanceController()
    controller.vehicle['doors'] = 'open'
    gate = DetectionGate(controller.detection_policy)
    bridge = VisionBusBridge()
    sessions = Sessions(Settings(data_dir=tmp_path), SimpleNamespace(cancel=lambda key: None), bridge, gate)
    session = sessions.create(Options(prompt='person'), 'live', 'outside')
    session.accept(1)
    admitted = gate.capture('outside')

    def close():
        controller.vehicle['doors'] = 'closing'
        controller._changed()
        if reopen:
            controller.vehicle['doors'] = 'open'
            controller._changed()

    backend = Backend(close if boundary == 'inference' else None)
    if boundary == 'queued':
        close()
    with pytest.raises(DetectionPausedError):
        result = sessions.infer(session, np.zeros((16, 16, 3), np.uint8), 1, 100, None,
                                backend, threading.Event(), 0, admitted['generation'])
        if boundary == 'publication':
            close()
        sessions.finalize(session, result, threading.Event())
    assert backend.calls == int(boundary != 'queued')
    assert session.count_voter.last_observed_ms is None
    assert bridge.snapshot()['sources']['outside']['frame_id'] is None


def test_inside_counts_and_tracker_continue_across_outside_door_gate(tmp_path):
    controller = AssistanceController()
    gate = DetectionGate(controller.detection_policy)
    bridge = VisionBusBridge()
    sessions = Sessions(Settings(data_dir=tmp_path), SimpleNamespace(cancel=lambda key: None), bridge, gate)
    session = sessions.create(Options(prompt='person', stabilization='off'), 'live', 'inside')

    class PeopleBackend:
        def detect(self, frame, options):
            return [Detection('person', [.1, .1, .4, .9], .95)]

    tracker, voter, generation = None, None, gate.capture('inside')['generation']
    for frame_id, doors in enumerate(['closed', 'opening', 'open', 'closing', 'closed'], 1):
        controller.vehicle['doors'] = doors
        session.accept(frame_id)
        result = sessions.infer(session, np.zeros((16, 16, 3), np.uint8), frame_id, frame_id * 100,
                                None, PeopleBackend(), threading.Event(), 0, generation)
        assert sessions.finalize(session, result, threading.Event())
        if tracker is None:
            tracker, voter = session.tracker, session.count_voter
        assert session.tracker is tracker
        assert session.count_voter is voter
        assert voter.last_observed_ms == frame_id * 100
        assert bridge.snapshot()['sources']['inside']['frame_id'] == frame_id
        assert gate.capture('inside')['generation'] == generation


def test_scenario_http_is_operator_only_and_rfid_capacity_is_atomic(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, backend='demo'), backend_factory=Demo, lan=True)
    assume_clear_standing_check(app.state.assistance)
    host = TestClient(app, client=('127.0.0.1', 51000))
    remote = TestClient(app, client=('192.168.1.70', 51001))
    now = [100.0]
    app.state.assistance.clock = lambda: now[0]
    assert remote.post('/api/assistance/scenario', json={'action': 'start'}).status_code == 403
    assert host.post('/api/assistance/scenario', json={
        'action': 'load', 'priority_occupied': 6, 'standard_occupied': 20}).status_code == 200
    assert host.post('/api/assistance/scenario', json={'action': 'start'}).status_code == 200
    now[0] += 1
    body = {'event_id': 'full-bus-tap', 'passenger_type': 'senior'}
    rejected = host.post('/api/bus/rfid', json=body)
    assert rejected.status_code == 200
    state = rejected.json()['scenario']
    assert not state['last_admission']['allowed']
    assert state['seats']['total'] == 26
    assert 'next bus' in state['announcement']['message']
    assert host.post('/api/bus/rfid', json=body).json()['scenario']['seats']['total'] == 26
    now[0] = 109.8
    assert host.get('/api/assistance/state').json()['scenario']['departure_notice']['announced']
    now[0] = 116
    assert host.get('/api/assistance/state').json()['vehicle']['doors'] == 'closing'
    now[0] = 117
    assert host.get('/api/assistance/state').json()['vehicle']['doors'] == 'closed'
    now[0] = 120
    assert host.get('/api/bus/state').json()['assistance']['vehicle']['motion'] == 'moving'
    outside = app.state.sessions.create(Options(prompt='person'), 'live', 'outside')
    paused = host.post(f'/api/sessions/{outside.id}/frames?frame_id=1&captured_at=1', content=b'unused')
    assert paused.status_code == 423
    assert paused.json()['code'] == 'journey_detection_paused'
    stopped = host.post('/api/assistance/scenario', json={'action': 'stop', 'stop_id': 'interchange'})
    assert stopped.status_code == 200
    assert not stopped.json()['scenario']['detection_enabled']
    assert stopped.json()['vehicle']['motion'] == 'braking'
    assert stopped.json()['vehicle']['stop_id'] == 'interchange'
    now[0] += 2
    opened = host.get('/api/assistance/state').json()
    assert not opened['scenario']['detection_enabled']
    assert opened['vehicle']['motion'] == 'stationary'
    assert opened['vehicle']['doors'] == 'opening'
    now[0] += .8
    opened = host.get('/api/assistance/state').json()
    assert opened['scenario']['detection_enabled']
    assert opened['vehicle']['doors'] == 'open'


@pytest.mark.parametrize('body', [
    {'action': 'load', 'priority_occupied': 7, 'standard_occupied': 0},
    {'action': 'load', 'priority_occupied': True, 'standard_occupied': 0},
    {'action': 'board', 'seat_type': 'invalid', 'event_id': 'tap'},
    {'action': 'start', 'force_depart': True},
])
def test_scenario_http_rejects_invalid_capacity_and_unknown_controls(tmp_path, body):
    app = create_app(Settings(data_dir=tmp_path, backend='demo'), backend_factory=Demo)
    host = TestClient(app, client=('127.0.0.1', 51000))
    assert host.post('/api/assistance/scenario', json=body).status_code == 422
