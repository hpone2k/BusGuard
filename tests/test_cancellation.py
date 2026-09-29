import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from vision.api import create_app
from vision.backends import Demo
from vision.config import Settings
from vision.schema import Options
from test_api import make_video, wait_until


class SlowDemo(Demo):
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def detect(self, image, options):
        self.entered.set()
        self.release.wait(5)
        return super().detect(image, options)


def test_deleting_session_rejects_in_flight_result(tmp_path):
    backend = SlowDemo()
    app = create_app(Settings(data_dir=tmp_path), backend_factory=lambda: backend)
    app.state.assistance.vehicle['doors'] = 'open'  # Admit outside/unassigned test frames.
    image = io.BytesIO(); Image.new('RGB', (32,32), 'red').save(image, format='PNG')
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        wait_until(lambda: client.get('/api/status').json(), lambda s: s['state']=='ready')
        id = client.post('/api/sessions', json={}).json()['id']
        request = pool.submit(client.post, f'/api/sessions/{id}/frames?frame_id=1&captured_at=1', content=image.getvalue())
        assert backend.entered.wait(2)
        assert client.delete(f'/api/sessions/{id}').status_code == 200
        backend.release.set()
        assert request.result(timeout=5).status_code == 409


def test_cancel_queued_and_active_video_jobs(tmp_path):
    backend = SlowDemo()
    app = create_app(Settings(data_dir=tmp_path/'data'), backend_factory=lambda: backend)
    app.state.assistance.vehicle['doors'] = 'open'  # Admit outside/unassigned test frames.
    content = make_video(tmp_path/'clip.mp4')
    with TestClient(app) as client:
        wait_until(lambda: client.get('/api/status').json(), lambda s:s['state']=='ready')
        ids = []
        for _ in range(2):
            video = client.post('/api/videos', content=content, headers={'X-Filename':'clip.mp4'}).json()
            ids.append(client.post('/api/jobs', json={'video_id':video['id']}).json()['id'])
        assert backend.entered.wait(2)
        assert client.post(f'/api/jobs/{ids[1]}/cancel').json()['state'] == 'canceled'
        client.post(f'/api/jobs/{ids[0]}/cancel')
        backend.release.set()
        result = wait_until(lambda: client.get(f'/api/jobs/{ids[0]}').json(), lambda j:j['state']=='canceled')
        assert result['error'] is None
        assert client.get(f'/api/jobs/{ids[0]}/timeline').status_code == 404


def test_retention_does_not_delete_active_jobs(tmp_path):
    backend = SlowDemo()
    app = create_app(Settings(data_dir=tmp_path/'data', retention_hours=0), backend_factory=lambda: backend)
    app.state.assistance.vehicle['doors'] = 'open'  # Admit outside/unassigned test frames.
    with TestClient(app) as client:
        wait_until(lambda: client.get('/api/status').json(), lambda s:s['state']=='ready')
        video = client.post('/api/videos', content=make_video(tmp_path/'clip.mp4'), headers={'X-Filename':'clip.mp4'}).json()
        id = client.post('/api/jobs', json={'video_id':video['id']}).json()['id']
        assert backend.entered.wait(2)
        app.state.jobs.cleanup()
        assert client.get(f'/api/jobs/{id}').status_code == 200
        client.post(f'/api/jobs/{id}/cancel'); backend.release.set()
        wait_until(lambda: client.get(f'/api/jobs/{id}').json(), lambda j:j['state']=='canceled')
        app.state.jobs.cleanup()
        assert client.get(f'/api/jobs/{id}').status_code == 404


def test_cancel_is_not_terminal_until_decoder_releases_file(tmp_path, monkeypatch):
    import cv2

    backend = SlowDemo()
    app = create_app(Settings(data_dir=tmp_path/'data', retention_hours=0), backend_factory=lambda: backend)
    app.state.assistance.vehicle['doors'] = 'open'  # Admit outside/unassigned test frames.
    releasing, allow_release = threading.Event(), threading.Event()
    original_capture = cv2.VideoCapture

    class HeldRelease:
        def __init__(self, *args):
            self.capture = original_capture(*args)

        def __getattr__(self, name):
            return getattr(self.capture, name)

        def release(self):
            releasing.set()
            allow_release.wait(5)
            self.capture.release()

    with TestClient(app) as client:
        wait_until(lambda: client.get('/api/status').json(), lambda s:s['state']=='ready')
        video = client.post('/api/videos', content=make_video(tmp_path/'clip.mp4'), headers={'X-Filename':'clip.mp4'}).json()
        monkeypatch.setattr(cv2, 'VideoCapture', HeldRelease)
        id = client.post('/api/jobs', json={'video_id':video['id']}).json()['id']
        try:
            assert backend.entered.wait(2)
            client.post(f'/api/jobs/{id}/cancel')
            backend.release.set()
            assert releasing.wait(2)
            assert client.get(f'/api/jobs/{id}').json()['state'] == 'canceling'
            assert client.delete(f'/api/jobs/{id}').status_code == 429
            app.state.jobs.cleanup()
            assert client.get(f'/api/jobs/{id}').status_code == 200
        finally:
            allow_release.set()
        wait_until(lambda: client.get(f'/api/jobs/{id}').json(), lambda j:j['state']=='canceled')
        app.state.jobs.cleanup()
        assert client.get(f'/api/jobs/{id}').status_code == 404
