import json
import threading

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from vision.api import create_app
from vision.bus_bridge import VisionBusBridge
from vision.config import Settings
from vision.schema import Detection, Options
from vision.scheduler import SupersededError
from vision.sessions import Sessions
from test_api import encoded_image, wait_until
from test_bus_bridge import Clock
from test_rtsp import rig, request, wait_for


def people(count):
    return [Detection('person', [.01 * index, .1, .01 * index + .005, .8], .92,
                      track_id=index).json() for index in range(count)]


class PeopleBackend:
    info = {'name': 'Test people detector', 'demo': False, 'phrases': False}

    def __init__(self):
        self.count = 7

    def warmup(self):
        pass

    def detect(self, frame, options):
        return [Detection(**detection) for detection in people(self.count)]


def test_live_api_returns_time_weighted_counts_and_source_replacement_resets(tmp_path):
    backend = PeopleBackend()
    app = create_app(Settings(data_dir=tmp_path), backend_factory=lambda: backend)
    body = {'kind': 'live', 'source_role': 'inside',
            'options': {'prompt': 'person, laptop', 'stabilization': 'off'}}
    with TestClient(app) as client:
        wait_until(lambda: client.get('/api/status').json(), lambda data: data['state'] == 'ready')
        session = client.post('/api/sessions', json=body).json()['id']
        for frame_id, (stamp, count) in enumerate(((0, 7), (700, 6), (900, 5), (1000, 5))):
            backend.count = count
            response = client.post(f'/api/sessions/{session}/frames?frame_id={frame_id}&captured_at={stamp}',
                                   content=encoded_image())
            assert response.status_code == 200, response.text
            summary = response.json()['count_summary']
        assert summary['total']['stable'] == 7
        assert summary['total']['raw'] == 5
        assert summary['classes']['person']['stable'] == 7
        assert summary['classes']['person']['support'] == pytest.approx(.7)
        assert summary['classes']['laptop']['stable'] == 0
        source = client.get('/api/bus/state').json()['sources']['inside']
        assert source['count_summary']['total'] == summary['total']
        for _ in range(3):
            polled = client.get('/api/bus/state').json()['sources']['inside']['count_summary']
            assert polled['coverage_ms'] == 1000
            assert polled['total']['distribution'] == summary['total']['distribution']
        replacement = client.post('/api/sessions', json=body).json()['id']
        assert client.get('/api/bus/state').json()['sources']['inside']['count_summary'] is None
        backend.count = 2
        fresh = client.post(f'/api/sessions/{replacement}/frames?frame_id=0&captured_at=1001',
                            content=encoded_image()).json()['count_summary']
        assert fresh['status'] == 'warming' and fresh['total']['stable'] is None
        assert fresh['total']['raw'] == 2
        # The old source can finish its own request but cannot replace the role's counts.
        client.post(f'/api/sessions/{session}/frames?frame_id=4&captured_at=1500', content=encoded_image())
        state = client.get('/api/bus/state').json()['sources']['inside']
        assert state['session_id'] == replacement and state['count_summary']['total']['raw'] == 2


def test_image_counts_are_instant_and_never_claim_temporal_support(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), backend_factory=PeopleBackend)
    app.state.assistance.vehicle['doors'] = 'open'  # Admit outside/unassigned test frames.
    with TestClient(app) as client:
        wait_until(lambda: client.get('/api/status').json(), lambda data: data['state'] == 'ready')
        session = client.post('/api/sessions', json={'kind': 'image', 'options': {'prompt': 'person'}}).json()['id']
        result = client.post(f'/api/sessions/{session}/frames?frame_id=0&captured_at=123',
                             content=encoded_image()).json()['count_summary']
        assert result['status'] == result['classes']['person']['status'] == 'instant'
        assert result['classes']['person']['raw'] == 7
        assert result['classes']['person']['support'] is None
        assert result['coverage_ms'] == 0


def test_recorded_video_gets_temporal_counts_but_keeps_recorded_provenance(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), backend_factory=PeopleBackend)
    with TestClient(app) as client:
        wait_until(lambda: client.get('/api/status').json(), lambda data: data['state'] == 'ready')
        response = client.post('/api/sessions', json={
            'kind': 'video', 'source_role': 'inside', 'source_name': 'Recorded cabin clip',
            'options': {'prompt': 'person', 'stabilization': 'off'}})
        assert response.status_code == 201, response.text
        session = response.json()['id']
        for frame_id, stamp in enumerate((0, 500, 1000)):
            response = client.post(f'/api/sessions/{session}/frames?frame_id={frame_id}&captured_at={stamp}',
                                   content=encoded_image())
            assert response.status_code == 200, response.text
        summary = response.json()['count_summary']
        assert summary['classes']['person']['stable'] == 7
        assert summary['classes']['person']['support'] == 1
        source = client.get('/api/bus/state').json()['sources']['inside']
        assert source['kind'] == 'video'
        assert source['source_name'] == 'Recorded cabin clip'
        assert source['count_summary']['classes']['person']['stable'] == 7


def test_canceled_and_superseded_results_do_not_enter_vote_history(tmp_path):
    sessions = Sessions(Settings(data_dir=tmp_path), None)
    session = sessions.create(Options(prompt='person'), 'live')
    canceled = threading.Event()
    session.accept(0)
    first = {'frame_id': 0, 'captured_at': 0, 'detections': people(7)}
    sessions.finalize(session, first, canceled)
    session.accept(1)
    canceled.set()
    with pytest.raises(SupersededError):
        sessions.finalize(session, {'frame_id': 1, 'captured_at': 700, 'detections': people(99)}, canceled)
    assert session.count_voter.snapshot()['last_observed_ms'] == 0
    canceled.clear()
    session.accept(2)
    with pytest.raises(SupersededError):
        sessions.finalize(session, {'frame_id': 1, 'captured_at': 900, 'detections': people(99)}, canceled)
    final = {'frame_id': 2, 'captured_at': 1000, 'detections': people(7)}
    sessions.finalize(session, final, canceled)
    assert final['count_summary']['total']['stable'] == 7
    assert final['count_summary']['total']['support'] == 1


def test_bridge_ages_frozen_counts_by_receipt_and_expires_before_source(tmp_path):
    clock = Clock()
    bridge = VisionBusBridge(clock.monotonic, clock.wall)
    sessions = Sessions(Settings(data_dir=tmp_path), None, bridge)
    session = sessions.create(Options(prompt='person'), 'live', 'inside')
    for frame_id, stamp in enumerate((0, 1000)):
        session.accept(frame_id)
        result = {'frame_id': frame_id, 'captured_at': stamp, 'detections': people(7)}
        sessions.finalize(session, result, threading.Event(), bridge.receipt())
    initial = bridge.snapshot()['sources']['inside']['count_summary']
    clock.advance(1)
    polled = bridge.snapshot()['sources']['inside']['count_summary']
    assert polled['total']['distribution'] == initial['total']['distribution']
    assert polled['age_ms'] == 1000
    clock.advance(.501)
    stale = bridge.snapshot()['sources']['inside']
    assert stale['connected']  # Counts expire earlier than the existing source status.
    assert stale['count_summary']['status'] == 'stale'
    assert stale['count_summary']['total']['raw'] is None
    assert stale['count_summary']['total']['stable'] is None
    assert stale['count_summary']['total']['distribution'] == []
    assert result['count_summary']['total']['stable'] == 7  # Public aging does not mutate inference results.
    receipt = bridge.receipt()
    clock.advance(1.501)
    session.accept(2)
    result = {'frame_id': 2, 'captured_at': 2000, 'detections': people(7)}
    sessions.finalize(session, result, threading.Event(), receipt)
    assert bridge.snapshot()['sources']['inside']['count_summary']['status'] == 'stale'


def test_repeated_or_reversed_capture_clock_cannot_refresh_accepted_counts(tmp_path):
    clock = Clock()
    bridge = VisionBusBridge(clock.monotonic, clock.wall)
    sessions = Sessions(Settings(data_dir=tmp_path), None, bridge)
    session = sessions.create(Options(prompt='person'), 'live', 'inside')
    for frame_id, stamp in enumerate((0, 1000)):
        session.accept(frame_id)
        sessions.finalize(session, {'frame_id': frame_id, 'captured_at': stamp,
                                   'detections': people(7)}, threading.Event(), bridge.receipt())
    original = bridge.snapshot()['sources']['inside']
    clock.advance(.6)
    for frame_id, stamp in ((2, 1000), (3, 999)):
        session.accept(frame_id)
        with pytest.raises(SupersededError, match='Frame capture timestamps must increase'):
            sessions.finalize(session, {'frame_id': frame_id, 'captured_at': stamp,
                                       'detections': people(5)}, threading.Event(), bridge.receipt())
        current = bridge.snapshot()['sources']['inside']
        assert current['frame_id'] == original['frame_id'] == 1
        assert current['received_at_ms'] == original['received_at_ms']
        assert current['count_summary']['total']['raw'] == 7
        assert current['count_summary']['age_ms'] == 600
        assert session.count_voter.last_observed_ms == 1000
    clock.advance(.901)
    assert bridge.snapshot()['sources']['inside']['count_summary']['status'] == 'stale'


def test_recorded_video_votes_use_media_time_not_processing_time(tmp_path):
    path = tmp_path / 'people.mp4'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, (96, 64))
    assert writer.isOpened()
    for _ in range(21):
        writer.write(np.zeros((64, 96, 3), np.uint8))
    writer.release()
    app = create_app(Settings(data_dir=tmp_path / 'data'), backend_factory=PeopleBackend)
    app.state.assistance.vehicle['doors'] = 'open'  # Admit outside/unassigned test frames.
    with TestClient(app) as client:
        wait_until(lambda: client.get('/api/status').json(), lambda data: data['state'] == 'ready')
        video = client.post('/api/videos', content=path.read_bytes(), headers={'X-Filename': 'people.mp4'}).json()
        job = client.post('/api/jobs', json={'video_id': video['id'],
            'options': {'prompt': 'person', 'stabilization': 'off'}, 'stride': 2}).json()
        finished = wait_until(lambda: client.get(f"/api/jobs/{job['id']}").json(),
                              lambda data: data['state'] not in {'queued', 'detecting'})
        assert finished['state'] == 'ready', finished
        rows = [json.loads(line) for line in client.get(f"/api/jobs/{job['id']}/timeline").text.splitlines()]
        assert rows[0]['count_summary']['status'] == 'warming'
        assert all(row['count_summary']['last_observed_ms'] == pytest.approx(row['time'] * 1000) for row in rows)
        assert rows[-1]['count_summary']['classes']['person']['stable'] == 7
        assert rows[-1]['count_summary']['coverage_ms'] == 1000


def test_rtsp_publication_includes_same_server_count_summary(rig):
    manager, _, processes, bridge, _ = rig
    manager.configure('inside', request())
    process = wait_for(lambda: processes and processes[0])
    process.frame(10)
    wait_for(lambda: manager.cameras['inside'].preview_frame_id == 1)
    summary = bridge.snapshot()['sources']['inside']['count_summary']
    assert summary['status'] == 'warming'
    assert summary['classes']['wheelchair']['raw'] == 1
    assert summary['classes']['person']['raw'] == 0
    assert bridge.snapshot()['sources']['outside']['count_summary'] is None
