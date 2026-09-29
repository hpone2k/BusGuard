import time

import cv2
import numpy as np
from fastapi.testclient import TestClient

from vision.api import create_app
from vision.config import Settings
from vision.rtsp import RTSPManager
from test_api import wait_until
from test_bus_bridge import SemanticBackend


def test_camera_api_keeps_credentials_private_and_validates_without_echo(tmp_path, monkeypatch):
    # Exercise the real request parser, session ownership, snapshots and teardown
    # without contacting any physical camera or spawning a decoder.
    monkeypatch.setattr(RTSPManager, '_capture', lambda self, camera: None)
    monkeypatch.setattr(RTSPManager, '_infer', lambda self, camera: None)
    app = create_app(Settings(data_dir=tmp_path), backend_factory=SemanticBackend)
    secret = 'private-camera-password'
    address = f'rtsp://operator:{secret}@192.168.1.23:554/live'
    with TestClient(app) as client:
        wait_until(lambda: client.get('/api/status').json(), lambda data: data['state'] == 'ready')
        for body in ({'url': address, 'name': ' '}, {'url': 'https://operator:' + secret + '@camera.local'},
                     {'url': address, 'options': {'size': 999}}, {'url': address, 'unknown': secret}):
            result = client.put('/api/bus/cameras/outside', json=body)
            assert result.status_code == 422, result.text
            assert secret not in result.text and address not in result.text
            assert 'input' not in result.text
        malformed = client.put('/api/bus/cameras/outside', content='{"url":"' + secret,
                               headers={'Content-Type': 'application/json'})
        assert malformed.status_code == 422 and secret not in malformed.text
        assert client.put('/api/bus/cameras/roof', json={'url': address}).status_code == 404
        assert client.put('/api/bus/cameras/outside', json={'url': address},
                          headers={'Origin': 'https://untrusted.example'}).status_code == 403
        result = client.put('/api/bus/cameras/outside', json={'url': address, 'name': 'Boarding CCTV'})
        assert result.status_code == 202, result.text
        assert result.json()['state'] == 'connecting'
        assert secret not in result.text and address not in result.text
        outside_id = result.json()['session_id']
        assert client.get('/api/bus/state').json()['sources']['outside']['session_id'] == outside_id
        assert client.get('/api/bus/cameras/outside').json()['name'] == 'Boarding CCTV'
        assert client.put('/api/bus/cameras/inside', json={'url': address, 'name': 'Cabin CCTV'}).status_code == 202
        cameras = client.get('/api/bus/cameras')
        assert len(cameras.json()['cameras']) == 2
        assert all(camera['enabled'] for camera in cameras.json()['cameras'])
        assert secret not in cameras.text and 'rtsp://' not in cameras.text
        assert client.get('/api/bus/cameras/outside/preview.jpg').status_code == 404
        camera = app.state.cameras.cameras['outside']
        _, encoded = cv2.imencode('.jpg', np.zeros((24, 32, 3), np.uint8))
        with camera.lock:
            camera.jpeg = encoded.tobytes()
            camera.preview_at = time.monotonic()
        preview = client.get('/api/bus/cameras/outside/preview.jpg')
        assert preview.status_code == 200 and preview.content == encoded.tobytes()
        assert preview.headers['content-type'] == 'image/jpeg'
        assert preview.headers['cache-control'] == 'no-store'
        with camera.lock:
            camera.preview_at -= 4
        assert client.get('/api/bus/cameras/outside/preview.jpg').status_code == 404
        assert client.delete('/api/bus/cameras/outside').json()['enabled'] is False
        assert not client.get('/api/bus/state').json()['sources']['outside']['connected']
        assert client.get('/api/bus/cameras/inside').json()['enabled'] is True
        inside_session = app.state.cameras.cameras['inside'].session
    assert inside_session.closed.is_set()
    assert app.state.cameras.snapshot()['cameras'][1]['enabled'] is False
