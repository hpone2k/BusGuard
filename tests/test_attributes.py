from collections import OrderedDict
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from vision.attributes import CAPABILITY, COLORS, match_clothing_color, parse_clothing_phrase, validate_yoloe_options
from vision.backends import YoloE
from vision.schema import Detection, Options
from vision.seating import seating_evidence


BOX = [.15, .05, .55, .95]
POLYGON = np.array([[.15,.05], [.55,.05], [.55,.95], [.15,.95]])
HSV = {'red': (0,220,210), 'orange': (16,220,210), 'yellow': (28,220,210),
       'green': (60,220,210), 'cyan': (90,220,210), 'blue': (115,220,210),
       'purple': (140,220,210), 'pink': (160,220,210), 'black': (0,0,25),
       'white': (0,0,230), 'gray': (0,0,120)}


def bgr(color):
    return cv2.cvtColor(np.uint8([[HSV[color]]]), cv2.COLOR_HSV2BGR)[0,0]


def portrait(color='red'):
    frame = np.full((240, 200, 3), 125, np.uint8)
    # Person-shaped foreground. Only the upper-clothing area has the target color.
    frame[12:228, 30:110] = bgr('blue')
    frame[55:126, 50:90] = bgr(color)
    return frame


@pytest.mark.parametrize('prompt,color', [('a person with a red shirt','red'), ('Person wearing a BLUE top','blue'),
                                        ('people in grey t-shirts','gray'), ('  a person   in a grey t-shirt  ','gray')])
def test_explicit_clothing_grammar(prompt, color):
    if 't-shirts' in prompt:
        with pytest.raises(ValueError):
            parse_clothing_phrase(prompt)
    else:
        assert parse_clothing_phrase(prompt).color == color


@pytest.mark.parametrize('prompt', ['person', 'a person holding a red shirt', 'a person with a red shirt and a bag',
                                  'a red car', 'person with a red and blue shirt', 'person behind a chair',
                                  'person wearing a red shirt near the door', 'person with red hair',
                                  'a person in a shirt', 'a pregnant person', 'senior passenger'])
def test_unsupported_phrases_fail_before_inference(prompt):
    with pytest.raises(ValueError, match='Clothing phrase mode supports'):
        parse_clothing_phrase(prompt)


def test_no_silent_object_fallback_for_descriptions():
    validate_yoloe_options(Options(prompt='person, laptop, wheelchair'))
    for prompt in ('a person with a red shirt', 'person in blue top', 'person holding a bag'):
        with pytest.raises(ValueError, match='Descriptions need clothing phrase mode'):
            validate_yoloe_options(Options(prompt=prompt))


@pytest.mark.parametrize('color', COLORS)
def test_segmented_torso_matches_each_supported_color(color):
    evidence = match_clothing_color(portrait(color), BOX, color, POLYGON)
    assert evidence is not None and evidence['color'] == color
    assert evidence['coverage'] > .95
    assert match_clothing_color(portrait(color), BOX, 'green' if color != 'green' else 'red', POLYGON) is None


def test_background_and_trouser_color_are_not_shirt_evidence():
    frame = np.full((240,200,3), bgr('red'), dtype=np.uint8)
    frame[12:228,30:110] = bgr('blue')
    assert match_clothing_color(frame, BOX, 'red', POLYGON) is None
    # A red object is inside the bbox but excluded by the narrower person mask.
    frame[55:126, 50:66] = bgr('red')
    narrower = np.array([[.335,.05], [.47,.05], [.47,.95], [.335,.95]])
    assert match_clothing_color(frame, BOX, 'red', narrower) is None


def test_ambiguous_mask_occlusion_small_crop_and_color_patch_do_not_pass():
    frame = portrait()
    assert match_clothing_color(frame, BOX, 'red', None) is None
    assert match_clothing_color(frame, BOX, 'red', np.array([[0,0],[1,1],[float('nan'),1]])) is None
    assert match_clothing_color(frame, BOX, 'red', POLYGON, [BOX]) is None
    assert match_clothing_color(frame, [.1,0,.6,.95], 'red', POLYGON) is None
    assert match_clothing_color(frame, [.1,.1,.12,.15], 'red', POLYGON) is None
    frame[90:126,50:90] = bgr('blue')
    assert match_clothing_color(frame, BOX, 'red', POLYGON) is None
    frame = portrait()
    frame[55:110,50:90] = bgr('blue')  # A small red lower patch is insufficient.
    assert match_clothing_color(frame, BOX, 'red', POLYGON) is None


class Tensor:
    def __init__(self, data): self.data = np.asarray(data)
    def cpu(self): return self
    def numpy(self): return self.data


def fake_yoloe(score=.8, mask=True):
    class Model:
        labels = None
        calls = 0
        def get_text_pe(self, labels): return labels
        def set_classes(self, labels, embeddings): self.labels = labels
        def predict(self, **kwargs):
            self.calls += 1
            self.kwargs = kwargs
            return [SimpleNamespace(boxes=SimpleNamespace(xyxyn=Tensor([BOX]),conf=Tensor([score]),cls=Tensor([0])),
                                    masks=SimpleNamespace(xyn=[POLYGON]) if mask else None)]
    backend = object.__new__(YoloE)
    backend.current, backend.embeddings = None, OrderedDict()
    backend.model, backend.device = Model(), 'cpu'
    return backend


def test_adapter_detects_person_then_filters_color_and_keeps_detector_score_and_phrase():
    phrase = 'a person with a red shirt'
    options = Options(prompt=phrase, mode='phrase', class_confidences={phrase:.7})
    backend = fake_yoloe()
    detections = backend.detect(portrait(), options)
    assert backend.model.labels == ['person']
    assert backend.model.calls == 1
    assert backend.model.kwargs['conf'] == .7
    assert len(detections) == 1 and detections[0].label == phrase and detections[0].score == .8
    assert backend.detect(portrait('blue'), options) == []
    assert backend.detect(portrait(), options.model_copy(update={'class_confidences':{phrase:.9}})) == []
    assert fake_yoloe(mask=False).detect(portrait(), options) == []
    assert CAPABILITY['phrase_scope'] == 'person-clothing-color'
    assert 'uncalibrated' in CAPABILITY['phrase_score']


def test_adapter_rejects_unsupported_phrase_without_running_model():
    backend = fake_yoloe()
    with pytest.raises(ValueError):
        backend.detect(portrait(), Options(prompt='a person holding a red shirt', mode='phrase'))
    assert backend.model.calls == 0


def test_clothing_subset_never_claims_everyone_is_seated():
    detections = [Detection('a person with a red shirt', BOX, .8, track_id=1)]
    result = seating_evidence(detections, detections, [], 1)
    assert not result['complete']
    assert result['seated'] == 0
