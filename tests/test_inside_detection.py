"""Inside object observations are continuous; posture belongs to a door epoch."""
import threading
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from vision.api import create_app
from vision.bus_bridge import VisionBusBridge
from vision.config import Settings
from vision.detection_gate import DetectionGate
from vision.schema import Detection, Options
from vision.seating import seating_evidence
from vision.sessions import Sessions


class Backend:
    info = {'name': 'test', 'demo': False, 'phrases': False}

    def __init__(self, after_detect=None, after_pose=None):
        self.calls, self.pose_calls = 0, 0
        self.after_detect, self.after_pose = after_detect, after_pose

    def warmup(self):
        pass

    def detect(self, frame, options):
        self.calls += 1
        if self.after_detect:
            self.after_detect()
        return [Detection('person', [.1, .1, .4, .9], .95)]

    def estimate_seating(self, frame, tracked, raw, timestamp):
        self.pose_calls += 1
        if self.after_pose:
            self.after_pose()
        return seating_evidence(tracked, raw, [], timestamp)


def system(tmp_path, kind='live', closed=True):
    policy = dict(enabled=False, generation='moving', inside_enabled=True,
                  inside_generation='inside-live', posture_enabled=closed, posture_generation=1)
    gate = DetectionGate(lambda: dict(policy))
    bridge = VisionBusBridge()
    sessions = Sessions(Settings(data_dir=tmp_path), SimpleNamespace(cancel=lambda key: None), bridge, gate)
    session = sessions.create(Options(prompt='person', posture_enabled=True, stabilization='off'), kind, 'inside')
    return policy, gate, bridge, sessions, session


def infer(sessions, session, backend, frame_id=1, **kwargs):
    session.accept(frame_id)
    return sessions.infer(session, np.zeros((32, 32, 3), np.uint8), frame_id, frame_id * 100,
                          None, backend, threading.Event(), 0, **kwargs)


@pytest.mark.parametrize('kind', ['live', 'image', 'video'])
@pytest.mark.parametrize('phase,closed', [('moving', True), ('braking', True), ('opening', False),
                                         ('boarding', False), ('closing', False)])
def test_inside_counts_continue_during_every_journey_phase(tmp_path, kind, phase, closed):
    policy, gate, bridge, sessions, session = system(tmp_path, kind, closed)
    policy['generation'] = phase
    backend = Backend()
    result = infer(sessions, session, backend)
    assert backend.calls == 1
    assert backend.pose_calls == int(closed)
    assert len(result['detections']) == 1
    assert sessions.finalize(session, result, threading.Event())
    public = bridge.snapshot()['sources']['inside']
    assert public['frame_id'] == 1
    assert public['count_summary'] is not None
    assert public['seating_summary']['status'] == ('observed' if closed else 'paused')
    assert '_posture_token' not in result


def test_inside_tracking_and_count_vote_continue_across_motion_and_door_changes(tmp_path):
    policy, gate, bridge, sessions, session = system(tmp_path)
    first = infer(sessions, session, Backend())
    sessions.finalize(session, first, threading.Event())
    tracker, voter = session.tracker, session.count_voter
    policy.update(enabled=True, generation='new-stop', posture_enabled=False, posture_generation=2)
    second = infer(sessions, session, Backend(), frame_id=2)
    sessions.finalize(session, second, threading.Event())
    assert session.tracker is tracker and session.count_voter is voter
    assert voter.last_observed_ms == 200
    assert second['seating_summary']['status'] == 'paused'


def test_queued_open_door_frame_does_not_run_pose_after_closure(tmp_path):
    policy, gate, bridge, sessions, session = system(tmp_path, closed=False)
    admission = gate.capture('inside')
    policy.update(posture_enabled=True, posture_generation=2)
    backend = Backend()
    result = infer(sessions, session, backend, generation=admission['generation'],
                   posture_token=admission['posture_token'])
    assert backend.calls == 1 and backend.pose_calls == 0
    assert result['seating_summary']['status'] == 'awaiting_fresh_frame'
    assert sessions.finalize(session, result, threading.Event())
    assert result['count_summary'] is not None


@pytest.mark.parametrize('boundary', ['detect', 'pose', 'publication'])
def test_posture_cannot_cross_a_door_transition_but_counts_publish(tmp_path, boundary):
    policy, gate, bridge, sessions, session = system(tmp_path)
    transition = lambda: policy.update(posture_enabled=False, posture_generation=2)
    backend = Backend(after_detect=transition if boundary == 'detect' else None,
                      after_pose=transition if boundary == 'pose' else None)
    result = infer(sessions, session, backend)
    if boundary == 'publication':
        transition()
    assert sessions.finalize(session, result, threading.Event())
    assert backend.pose_calls == int(boundary != 'detect')
    assert result['seating_summary']['status'] == 'paused'
    assert not result['seating_summary']['complete']
    assert all('landmarks' not in row for row in result['seating_summary']['occupants'])
    assert bridge.snapshot()['sources']['inside']['count_summary'] is not None


def test_full_open_close_cycle_discards_previous_closed_epoch(tmp_path):
    policy, gate, bridge, sessions, session = system(tmp_path)
    result = infer(sessions, session, Backend())
    policy.update(posture_enabled=True, posture_generation=3)
    assert sessions.finalize(session, result, threading.Event())
    assert result['seating_summary']['status'] == 'awaiting_fresh_frame'
    fresh = infer(sessions, session, Backend(), frame_id=2)
    assert fresh['seating_summary']['status'] == 'observed'
    assert fresh['seating_summary']['door_generation'] == 3


def test_http_inside_accepts_moving_frames_and_outside_is_paused(tmp_path):
    backend = Backend()
    app = create_app(Settings(data_dir=tmp_path, backend='demo'), backend_factory=lambda: backend)
    policy = dict(enabled=False, generation='moving', inside_enabled=True,
                  inside_generation='inside-live', posture_enabled=True, posture_generation=7)
    app.state.sessions.detection_gate.policy = lambda: dict(policy)
    with TestClient(app, client=('127.0.0.1', 55000)) as host:
        deadline = time.monotonic() + 3
        while host.get('/api/status').json()['state'] != 'ready':
            assert time.monotonic() < deadline
            time.sleep(.01)
        payload = cv2.imencode('.png', np.zeros((32, 32, 3), np.uint8))[1].tobytes()
        for role, expected in [('inside', 200), ('outside', 423)]:
            session = app.state.sessions.create(Options(prompt='person', posture_enabled=True), 'live', role)
            response = host.post(f'/api/sessions/{session.id}/frames?frame_id=1&captured_at=100', content=payload)
            assert response.status_code == expected, response.text
            if role == 'inside':
                assert response.json()['seating_summary']['door_generation'] == 7
                assert response.json()['count_summary'] is not None
        assert backend.calls == backend.pose_calls == 1
