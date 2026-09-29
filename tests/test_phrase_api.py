from fastapi.testclient import TestClient

from vision.api import create_app
from vision.attributes import CAPABILITY
from vision.config import Settings
from test_api import wait_until


class AttributeBackend:
    info = {'name': 'Test attribute detector', 'demo': False, **CAPABILITY}

    def warmup(self):
        pass

    def detect(self, frame, options):
        return []


def test_phrase_validation_precedes_session_or_camera_creation(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), backend_factory=AttributeBackend)
    with TestClient(app) as client:
        status = wait_until(lambda: client.get('/api/status').json(), lambda data: data['state'] == 'ready')
        assert status['backend']['phrase_scope'] == 'person-clothing-color'
        good = {'mode': 'phrase', 'prompt': 'a person with a red shirt'}
        assert client.post('/api/sessions', json={'options': good}).status_code == 201
        bad = {'mode': 'phrase', 'prompt': 'a person holding a red bag'}
        for endpoint, body in (
            ('/api/sessions', {'options': bad}),
            ('/api/bus/cameras/outside', {'url': 'rtsp://test:private-test-secret@camera.local/stream', 'options': bad})
        ):
            response = (client.put if 'cameras' in endpoint else client.post)(endpoint, json=body)
            assert response.status_code == 400, response.text
            assert 'not supported' in response.text
            assert 'private-test-secret' not in response.text
        assert not client.get('/api/bus/cameras/outside').json()['enabled']
        assert len(app.state.sessions.items) == 1
