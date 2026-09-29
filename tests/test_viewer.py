from fastapi.testclient import TestClient

from vision.viewer import create_viewer_app


def test_separate_viewer_reads_current_state_and_cannot_mutate_cameras():
    state = {'instance_id': 'real-service', 'sources': {'inside': None, 'outside': None}, 'events': []}
    app = create_viewer_app(fetch_state=lambda: state)
    with TestClient(app) as client:
        assert client.get('/').status_code == 200
        assert client.get('/api/bus/state').json() == state
        state['revision'] = 2
        response = client.get('/api/bus/state')
        assert response.json()['revision'] == 2
        assert response.headers['Cache-Control'] == 'no-store'
        assert client.put('/api/bus/cameras/inside', json={}).status_code == 404
        assert client.get('/api/bus/state', headers={'Host': 'evil.example'}).status_code == 400


def test_viewer_failure_never_reuses_last_passenger_count():
    failed = False

    def fetch():
        if failed:
            raise ConnectionError('internal diagnostic must not be exposed')
        return {'sources': {'inside': {'count': 7}}}

    with TestClient(create_viewer_app(fetch_state=fetch)) as client:
        assert client.get('/api/bus/state').status_code == 200
        failed = True
        response = client.get('/api/bus/state')
        assert response.status_code == 503
        assert 'sources' not in response.json()
        assert 'internal diagnostic' not in response.text
