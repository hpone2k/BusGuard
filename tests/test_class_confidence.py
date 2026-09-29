from collections import OrderedDict
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from vision.api import create_app
from vision.backends import YoloE
from vision.config import Settings
from vision.schema import Detection, Options
from vision.tracking import StableTracker
from test_api import encoded_image, make_video, wait_until


class ArrayResult:
    def __init__(self, value): self.value = np.asarray(value)
    def cpu(self): return self
    def numpy(self): return self.value


class TestModel:
    __test__ = False
    def __init__(self): self.threshold = None
    def get_text_pe(self, labels): return None
    def set_classes(self, labels, embedding): self.labels = labels
    def predict(self, **args):
        self.threshold = args['conf']
        boxes = SimpleNamespace(xyxyn=ArrayResult([[.1,.1,.3,.6],[.5,.4,.7,.6],[.7,.1,.8,.3]]),
                                conf=ArrayResult([.6,.3,.5]), cls=ArrayResult([0,1,2]))
        return [SimpleNamespace(boxes=boxes)]


def scored_backend():
    # Exercise the real YOLOE adapter and API; only replace expensive model inference.
    backend = object.__new__(YoloE)
    backend.model, backend.device = TestModel(), 'cpu'
    backend.current, backend.embeddings = None, OrderedDict()
    backend.info = {'name':'test', 'scores':True, 'phrases':False, 'demo':False, 'device':'cpu'}
    backend.warmup = lambda: None
    return backend


def test_class_threshold_validation_and_legacy_fallback():
    options = Options(prompt='Person, laptop, bottle', confidence=.4,
                      class_confidences={' PERSON ': .8, 'laptop': .2})
    assert options.confidence_for('person') == .8
    assert options.confidence_for('LAPTOP') == .2
    assert options.confidence_for('bottle') == .4
    assert options.minimum_confidence == .2
    assert Options(confidence=.6).confidence_for('person') == .6
    for values in [{'cat':.2}, {'person':float('nan')}, {'person':.99}, {'person':.01}, {'person':.2,'Person':.3}]:
        with pytest.raises(ValueError): Options(class_confidences=values)
    phrase = Options(mode='phrase', prompt='person, wearing red', class_confidences={'person, wearing red':.8})
    assert phrase.confidence_for('person') == .8


def test_yoloe_keeps_low_threshold_class_before_applying_stricter_class_filter():
    backend = scored_backend()
    options = Options(prompt='person, laptop, bottle', class_confidences={'person':.8,'laptop':.2,'bottle':.7})
    detections = backend.detect(np.zeros((64,64,3), np.uint8), options)
    assert backend.model.threshold == .2  # A global .35 cutoff would lose the laptop.
    assert [d.label for d in detections] == ['laptop']
    options = options.model_copy(update={'class_confidences':{'person':.5,'laptop':.4,'bottle':.4}})
    assert [d.label for d in backend.detect(np.zeros((64,64,3), np.uint8), options)] == ['person', 'bottle']


def test_tracking_has_separate_creation_and_recovery_thresholds():
    options = Options(prompt='person, laptop', class_confidences={'person':.8,'laptop':.2})
    tracker = StableTracker(options)
    assert tracker.detection_options.confidence_for('person') == .15
    assert tracker.detection_options.confidence_for('laptop') == .1
    def items(person_score, laptop_score):
        return [Detection('person',[.1,.1,.3,.6],person_score), Detection('laptop',[.5,.4,.7,.6],laptop_score)]
    assert tracker.update(items(.6,.3),0) == []
    found = tracker.update(items(.6,.3),.033)
    assert [d.label for d in found] == ['laptop']
    laptop_id = found[0].track_id
    tracker.update(items(.9,.18),.067)
    found = tracker.update(items(.9,.18),.1)
    assert {d.label for d in found} == {'person','laptop'}
    assert next(d for d in found if d.label == 'laptop').track_id == laptop_id
    assert not any(d.predicted for d in found)
    raw = StableTracker(options.model_copy(update={'stabilization':'off'}))
    assert [d.label for d in raw.update(items(.6,.3),0)] == ['laptop']


@pytest.fixture
def scored_client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path/'data'), backend_factory=scored_backend)
    app.state.assistance.vehicle['doors'] = 'open'  # Admit outside/unassigned test frames.
    with TestClient(app) as client:
        wait_until(lambda: client.get('/api/status').json(), lambda s:s['state']=='ready')
        yield client


def test_image_cache_separates_per_class_options(scored_client):
    image = encoded_image()
    def detect(overrides):
        response = scored_client.post('/api/sessions', json={'options':{'prompt':'person, laptop, bottle','class_confidences':overrides}})
        assert response.status_code == 201
        id = response.json()['id']
        response = scored_client.post(f'/api/sessions/{id}/frames?frame_id=0&captured_at=0', content=image)
        assert response.status_code == 200, response.text
        scored_client.delete(f'/api/sessions/{id}')
        return response.json()
    first = detect({'person':.8,'laptop':.2,'bottle':.7})
    assert [d['label'] for d in first['detections']] == ['laptop']
    second = detect({'person':.5,'laptop':.4,'bottle':.7})
    assert [d['label'] for d in second['detections']] == ['person'] and not second['cached']
    assert detect({'person':.5,'laptop':.4,'bottle':.7})['cached']
    assert scored_client.post('/api/sessions', json={'options':{'class_confidences':{'unknown':.5}}}).status_code == 422


def test_video_job_persists_and_applies_per_class_confidence(scored_client, tmp_path):
    response = scored_client.post('/api/videos', content=make_video(tmp_path/'clip.mp4'), headers={'X-Filename':'clip.mp4'})
    video_id = response.json()['id']
    thresholds = {'person':.8,'laptop':.2,'bottle':.7}
    response = scored_client.post('/api/jobs', json={'video_id':video_id,'options':{
        'prompt':'person, laptop, bottle','class_confidences':thresholds}})
    assert response.status_code == 202, response.text
    id = response.json()['id']
    job = wait_until(lambda: scored_client.get(f'/api/jobs/{id}').json(), lambda j:j['state'] not in {'queued','detecting'})
    assert job['state'] == 'ready', job
    assert job['options']['class_confidences'] == thresholds
    import json
    rows = [json.loads(line) for line in scored_client.get(f'/api/jobs/{id}/timeline').text.splitlines()]
    assert rows[0]['detections'] == []
    assert all([d['label'] for d in row['detections']] == ['laptop'] for row in rows[1:])
    saved = json.loads((scored_client.app.state.jobs.directory(id)/'status.json').read_text())
    assert saved['options']['class_confidences'] == thresholds
