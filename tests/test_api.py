import io
import json
import time
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from vision.api import create_app
from vision.backends import Demo
from vision.config import Settings
from vision.jobs import VideoJobs
from vision.media import ffmpeg_path, subprocess_flags


def wait_until(call, predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while True:
        result = call()
        if predicate(result): return result
        assert time.monotonic() < deadline, result
        time.sleep(.02)


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(backend='demo', data_dir=tmp_path / 'data'), backend_factory=Demo)
    app.state.assistance.vehicle['doors'] = 'open'  # Admit outside/unassigned test frames.
    with TestClient(app) as client:
        wait_until(lambda: client.get('/api/status').json(), lambda s: s['state'] == 'ready')
        yield client


def encoded_image():
    array = np.zeros((64, 96, 3), np.uint8)
    array[10:35, 20:45] = [255, 50, 20]
    buffer = io.BytesIO(); Image.fromarray(array).save(buffer, format='PNG')
    return buffer.getvalue()


def session(client, **body):
    response = client.post('/api/sessions', json=body)
    assert response.status_code == 201, response.text
    return response.json()['id']


def test_binary_image_cache_and_session_invalidation(client):
    id = session(client)
    path = f'/api/sessions/{id}/frames'
    first = client.post(path + '?frame_id=1&captured_at=100', content=encoded_image())
    assert first.status_code == 200, first.text
    result = first.json()
    assert len(result['detections']) == 1 and result['inference_ms'] >= 0
    assert result['detections'][0]['score'] is None
    second = client.post(path + '?frame_id=2&captured_at=101', content=encoded_image()).json()
    assert second['cached'] is True
    assert client.post(path + '?frame_id=1&captured_at=100', content=encoded_image()).status_code == 409
    assert client.delete(f'/api/sessions/{id}').status_code == 200
    assert client.post(path + '?frame_id=3&captured_at=103', content=encoded_image()).status_code == 404


def test_invalid_inputs_and_origin(client):
    id = session(client)
    assert client.post(f'/api/sessions/{id}/frames?frame_id=1&captured_at=1', content=b'bad image').status_code == 400
    assert client.post('/api/sessions', json={}, headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.get('/api/status', headers={'Host':'evil.example'}).status_code == 400
    assert client.post('/api/sessions', json={'options':{'mode':'phrase'}}).status_code == 400
    assert client.get('/api/jobs/not-found').status_code == 404
    assert client.post('/api/videos', content=b'bad video', headers={'X-Filename':'bad.mp4'}).status_code == 400
    assert not list(client.app.state.jobs.media_dir.glob('*.mp4'))


def make_video(path):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, (96, 64))
    assert writer.isOpened()
    for i in range(10):
        frame = np.zeros((64,96,3), np.uint8)
        if i < 4 or i > 7:
            frame[15:35, 10+i:30+i] = [30,40,240]
        writer.write(frame)
    writer.release()
    return path.read_bytes()


def uploaded(client, tmp_path):
    content = make_video(tmp_path / 'clip.mp4')
    response = client.post('/api/videos', content=content, headers={'X-Filename':'clip.mp4'})
    assert response.status_code == 201, response.text
    return response.json()


def completed_job(client, tmp_path):
    video = uploaded(client, tmp_path)
    response = client.post('/api/jobs', json={'video_id':video['id']})
    assert response.status_code == 202, response.text
    id = response.json()['id']
    result = wait_until(lambda: client.get(f'/api/jobs/{id}').json(), lambda j: j['state'] not in {'queued','detecting'})
    assert result['state'] == 'ready', result
    return id, video


def test_video_analysis_empty_frames_range_and_deletion(client, tmp_path):
    id, video = completed_job(client, tmp_path)
    result = client.get(f'/api/jobs/{id}/timeline')
    rows = [json.loads(line) for line in result.text.splitlines()]
    assert len(rows) == 10
    assert rows[5]['detections'] == []
    assert rows[0]['detections'] == []  # New tracks need confirmation.
    assert rows[1]['detections']
    assert client.get(f'/api/videos/{video["id"]}/source', headers={'Range':'bytes=0-99'}).status_code == 206
    assert client.delete(f'/api/videos/{video["id"]}').status_code == 429
    assert client.delete(f'/api/jobs/{id}').status_code == 200
    assert client.delete(f'/api/videos/{video["id"]}').status_code == 200


def test_failed_encoder_does_not_publish_success(client, tmp_path, monkeypatch):
    id, _ = completed_job(client, tmp_path)
    codecs = []
    def fail(job, height, codec):
        codecs.append(codec); raise RuntimeError('encoder unavailable')
    monkeypatch.setattr(client.app.state.jobs, '_encode', fail)
    assert client.post(f'/api/jobs/{id}/export').status_code == 202
    job = wait_until(lambda: client.get(f'/api/jobs/{id}').json(), lambda j: j['state'] == 'ready')
    assert codecs == ['h264_nvenc', 'libx264']
    assert job['export_ready'] is False
    assert 'encoder unavailable' in job['export_error']
    assert client.get(f'/api/jobs/{id}/export').status_code == 404


def test_real_video_export(client, tmp_path):
    id, _ = completed_job(client, tmp_path)
    assert client.post(f'/api/jobs/{id}/export').status_code == 202
    job = wait_until(lambda: client.get(f'/api/jobs/{id}').json(), lambda j: j['state'] == 'ready', seconds=30)
    assert job['export_error'] is None, job
    assert job['export_ready'] is True
    response = client.get(f'/api/jobs/{id}/export')
    assert response.status_code == 200 and len(response.content) > 512


def test_oversized_body_rejected_before_decode(tmp_path):
    app = create_app(Settings(backend='demo', data_dir=tmp_path, image_bytes=50), backend_factory=Demo)
    app.state.assistance.vehicle['doors'] = 'open'  # Admit outside/unassigned test frames.
    with TestClient(app) as client:
        wait_until(lambda: client.get('/api/status').json(), lambda j: j['state'] == 'ready')
        id = session(client)
        response = client.post(f'/api/sessions/{id}/frames?frame_id=1&captured_at=1', content=b'x'*51)
        assert response.status_code == 413


def test_recovery_marks_interrupted_jobs_failed(tmp_path):
    root = tmp_path / 'data'; jobdir = root / 'jobs' / ('a' * 32); jobdir.mkdir(parents=True)
    record = {'id':'a'*32, 'video_id':'b'*32, 'state':'detecting', 'created':time.time(), 'error':None}
    (jobdir/'status.json').write_text(json.dumps(record))
    jobs = VideoJobs(Settings(data_dir=root), None)
    try: assert jobs.snapshot('a'*32)['state'] == 'failed'
    finally: jobs.close()


def test_export_preserves_audio(client, tmp_path):
    make_video(tmp_path / 'silent.mp4')
    ffmpeg = ffmpeg_path(Settings())
    source = tmp_path / 'audio.mp4'
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(tmp_path/'silent.mp4'),
                    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-c:v', 'copy', '-c:a', 'aac',
                    '-shortest', str(source)], check=True, capture_output=True, **subprocess_flags())
    video = client.post('/api/videos', content=source.read_bytes(), headers={'X-Filename':'audio.mp4'}).json()
    job = client.post('/api/jobs', json={'video_id':video['id']}).json()
    id = job['id']
    wait_until(lambda: client.get(f'/api/jobs/{id}').json(), lambda j:j['state']=='ready')
    client.post(f'/api/jobs/{id}/export')
    exported = wait_until(lambda: client.get(f'/api/jobs/{id}').json(), lambda j:j['state']=='ready', seconds=30)
    assert exported['export_ready'], exported
    output = client.app.state.jobs.directory(id) / 'export.mp4'
    check = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', str(output), '-map', '0:a:0',
                            '-f', 'null', '-'], capture_output=True, **subprocess_flags())
    assert check.returncode == 0, check.stderr.decode(errors='replace')
