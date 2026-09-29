"""Standing-only evidence never manufactures a sitting classification."""
from collections import OrderedDict
import threading
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from vision.backends import YoloE
from vision.config import Settings
from vision.departure import DepartureInterlock
from vision.schema import Detection, Options, filter_confidence
from vision.sessions import Sessions
from vision.standing import YoloStanding, estimate


BOX = [.1, .1, .4, .9]


def person(identity=7, box=None, **changes):
    return dict(label='person', bbox=list(BOX if box is None else box), track_id=identity,
                score=.9, predicted=False) | changes


def standing(box=None, **changes):
    return dict(label='standing person', bbox=list(BOX if box is None else box), score=.9,
                predicted=False) | changes


def packet(summary, index, now, count=1):
    return dict(kind='live', connected=True, is_demo=False, session_id='inside', frame_id=index,
                age_ms=0, received_at_ms=now * 1000, seating_summary=summary,
                count_summary={'classes': {'person': {'status': 'stable', 'stable': count}}})


def test_healthy_negative_is_not_a_seated_classification():
    tracked, raw = [person()], [person(None)]
    summary = estimate(tracked, raw, [], 1000)
    assert summary['mode'] == 'standing_only' and summary['engine'] == 'YOLOE'
    assert summary['status'] == 'observed' and summary['complete'] and summary['clear']
    assert summary['people'] == summary['not_detected'] == 1
    assert summary['standing'] == summary['unknown'] == 0 and summary['seated'] is None
    assert summary['occupants'][0]['posture'] == 'not_detected'
    assert 'sitting' in summary['reason'].lower()
    assert tracked[0]['track_id'] == 7 and raw[0]['track_id'] is None


def test_matched_standing_positive_holds_departure():
    gate = DepartureInterlock()
    summary = estimate([person()], [person(None)], [standing()], 1000000)
    gate.update(packet(summary, 1, 1000), closed=True, enabled=True, now=1000)
    assert summary['standing'] == 1 and not summary['clear']
    assert summary['seated'] is None
    assert gate.result(1000)['status'] == 'standing'
    assert not gate.result(1000)['can_depart']


def test_five_seconds_of_fresh_healthy_negatives_can_clear_without_sitting_data():
    gate = DepartureInterlock()
    for index in range(15):
        now = 1000 + index * .4
        summary = estimate([person()], [person(None)], [], now * 1000)
        gate.update(packet(summary, index, now), closed=True, enabled=True, now=now)
    assert gate.result(now)['can_depart']
    assert gate.result(now)['mode'] == 'standing_only'
    assert 'No standing detected' in gate.result(now)['message']
    # A newly detected standing person resets readiness immediately.
    now += .4
    summary = estimate([person()], [person(None)], [standing()], now * 1000)
    gate.update(packet(summary, 15, now), closed=True, enabled=True, now=now)
    assert gate.result(now)['status'] == 'standing' and gate.result(now)['progress_ms'] == 0


def test_untracked_raw_person_and_matching_positive_are_one_occupant():
    summary = estimate([], [person(None)], [standing()], 1000)
    assert summary['people'] == summary['standing'] == 1
    assert summary['unknown'] == 0
    assert summary['occupants'][0]['track_id'] is None
    assert not summary['complete'] and not summary['clear']
    assert summary['raw_person_count'] == summary['standing_detection_count'] == 1


def test_semantic_only_standing_positive_still_holds_without_changing_generic_count():
    summary = estimate([], [], [standing()], 1000)
    assert summary['standing'] == summary['people'] == 1
    assert summary['raw_person_count'] == 0 and summary['seated'] is None
    assert not summary['clear'] and not summary['complete']


def test_duplicate_standing_boxes_do_not_duplicate_a_person():
    summary = estimate([person()], [person(None)], [standing(), standing(), standing(label=' standing ')], 1000)
    assert summary['people'] == summary['standing'] == summary['standing_detection_count'] == 1
    assert summary['complete'] and not summary['clear']


@pytest.mark.parametrize('box', [[.4, .1, .1, .9], [-.1, .1, .4, .9], [0, 0, 0, .9],
                                [float('nan'), .1, .4, .9], [True, .1, .4, .9], [.1, .4]])
def test_invalid_positive_boxes_never_become_healthy_negative(box):
    summary = estimate([person()], [person(None)], [standing(box)], 1000)
    assert not summary['complete'] and not summary['clear']
    assert summary['unknown'] == 1 and summary['standing'] == 0


@pytest.mark.parametrize('tracked,raw', [
    ([person(predicted=True)], [person(None)]),
    ([person()], [person(None, predicted=True)]),
    ([person(None)], [person(None)]),
    ([person(), person()], [person(None), person(None)]),
])
def test_generic_tracking_uncertainty_does_not_invalidate_successful_standing_pass(tracked, raw):
    summary = estimate(tracked, raw, [], 1000)
    assert not summary['complete'] and summary['clear'] and summary['standing_check_valid']
    assert summary['unknown'] > 0
    assert summary['seated'] is None


@pytest.mark.parametrize('positive', [None, {}, 'standing', standing(predicted=True),
                                    standing(label='person'), standing(score=None),
                                    standing(score=float('nan')), standing(score=True)])
def test_malformed_standing_result_never_becomes_valid_zero(positive):
    summary = estimate([person()], [person(None)], [positive], 1000)
    assert not summary['standing_check_valid'] and not summary['clear']


def test_malformed_generic_geometry_remains_invalid():
    summary = estimate([person(box=[])], [person(None)], [], 1000)
    assert not summary['standing_check_valid'] and not summary['clear']


@pytest.mark.parametrize('tracked,raw,positives', [(None, [], []), ([], None, []), ([], [], None)])
def test_invalid_inputs_are_unavailable(tracked, raw, positives):
    summary = estimate(tracked, raw, positives, 1000)
    assert summary['status'] == 'unavailable' and not summary['clear']
    assert not summary['standing_check_valid']


def test_unavailable_clears_positive_and_never_looks_healthy():
    summary = estimate([person()], [person(None)], [standing()], 1000, False, 'Camera model failed.')
    assert summary['status'] == 'unavailable' and summary['reason'] == 'Camera model failed.'
    assert summary['standing'] == 0 and summary['unknown'] == 1
    assert not summary['clear'] and not summary['complete'] and summary['seated'] is None
    assert not summary['standing_check_valid']


def test_empty_cabin_is_zero_standing_without_fabricated_sitting():
    summary = estimate([], [], [], 1000)
    assert summary['people'] == summary['standing'] == summary['unknown'] == 0
    assert summary['status'] == 'observed' and summary['clear'] and summary['complete']
    assert summary['seated'] is None and summary['occupants'] == []


def test_accounting_limit_cannot_produce_clearance():
    summary = estimate([], [person(None)] * 301, [], 1000)
    assert summary['capacity_reached'] and len(summary['occupants']) <= 300
    assert not summary['clear'] and not summary['complete']


class Detector:
    info_model = 'test-yoloe.pt'

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def detect(self, image, options):
        self.calls.append(options)
        if self.fail:
            raise RuntimeError('synthetic inference failure')
        return [Detection('standing person', list(BOX), .9)]


def test_adapter_asks_only_for_standing_and_keeps_generic_options_unchanged():
    detector = Detector()
    adapter = YoloStanding(detector)
    image = np.zeros((16, 16, 3), np.uint8)
    generic = Options(prompt='person, wheelchair, laptop', confidence=.55, class_confidences={'person': .65})
    original = generic.model_dump()
    detector.detect(image, generic)
    adapter.warmup(image)
    result = adapter.estimate(image, [person()], [person(None)], 1000)
    assert detector.calls[0] is generic and generic.model_dump() == original
    assert all(option.categories() == ['standing person'] for option in detector.calls[1:])
    assert all(option.mode == 'objects' and option.confidence == .10 for option in detector.calls[1:])
    assert result['standing'] == 1 and result['seated'] is None
    assert adapter.info['model'] == 'test-yoloe.pt' and not adapter.info['asynchronous']
    assert adapter.info['threshold'] == result['threshold'] == Options().standing_confidence == .10


def test_standing_threshold_controls_low_score_positives_independently_of_person_threshold():
    class ScoredDetector(Detector):
        def detect(self, image, options):
            self.calls.append(options)
            return filter_confidence([Detection('standing person', list(BOX), .17)], options)
    detector = ScoredDetector()
    adapter = YoloStanding(detector)
    image = np.zeros((16, 16, 3), np.uint8)
    low = adapter.estimate(image, [person()], [person(None)], 1000)
    high = adapter.estimate(image, [person()], [person(None)], 1100, confidence=.25)
    assert low['standing'] == 1 and low['threshold'] == .10
    assert high['standing'] == 0 and high['threshold'] == .25
    assert low['seated'] is high['seated'] is None
    assert adapter.options.confidence == .10  # Per-source changes do not leak into the default.


@pytest.mark.parametrize('confidence', [-1, 0, .049, .951, 1, float('inf'), float('-inf'), float('nan')])
def test_standing_confidence_rejects_out_of_range_and_nonfinite_values(confidence):
    with pytest.raises(ValidationError):
        Options(prompt='person', standing_confidence=confidence)


@pytest.mark.parametrize('confidence', [.05, .12, .95])
def test_session_forwards_standing_confidence_without_changing_object_thresholds(tmp_path, confidence):
    class Backend(Detector):
        posture_options = True

        def __init__(self):
            super().__init__()
            self.standing = YoloStanding(self)

        def detect(self, image, options):
            self.calls.append(options)
            label = 'standing person' if options.categories() == ['standing person'] else 'person'
            return [Detection(label, list(BOX), .9)]

        def estimate_seating(self, image, tracked, raw, captured_at_ms, **kwargs):
            return self.standing.estimate(image, tracked, raw, captured_at_ms, **kwargs)

    options = Options(prompt='person, laptop', posture_enabled=True, standing_confidence=confidence,
                      confidence=.55, class_confidences={'person': .65})
    sessions = Sessions(Settings(data_dir=tmp_path), None)
    session = sessions.create(options, 'image', 'inside')
    session.accept(1)
    backend = Backend()
    result = sessions.infer(session, np.zeros((16, 16, 3), np.uint8), 1, 1000, None,
                            backend, threading.Event(), 0)
    generic, standing_options = backend.calls
    assert generic is options and generic.confidence_for('person') == .65
    assert generic.confidence_for('laptop') == .55
    assert standing_options.categories() == ['standing person'] and standing_options.confidence == confidence
    assert standing_options.class_confidences == {}
    assert result['seating_summary']['threshold'] == confidence
    assert result['seating_summary']['seated'] is None
    assert session.options.confidence == .55 and session.options.class_confidences == {'person': .65}


def test_warmup_failure_disables_posture_but_does_not_retry_model_on_every_frame():
    detector = Detector(fail=True)
    adapter = YoloStanding(detector)
    image = np.zeros((16, 16, 3), np.uint8)
    adapter.warmup(image)
    result = adapter.estimate(image, [person()], [person(None)], 1000)
    assert not adapter.info['available'] and len(detector.calls) == 1
    assert result['status'] == 'unavailable' and not result['clear']


def test_frame_failure_is_unknown_then_next_healthy_frame_can_recover():
    detector = Detector(fail=True)
    adapter = YoloStanding(detector)
    image = np.zeros((16, 16, 3), np.uint8)
    failed = adapter.estimate(image, [person()], [person(None)], 1000)
    assert failed['status'] == 'unavailable' and failed['unknown'] == 1 and not failed['clear']
    detector.fail = False
    recovered = adapter.estimate(image, [person()], [person(None)], 1100)
    assert recovered['status'] == 'observed' and recovered['standing'] == 1


def test_shared_yolo_model_restores_generic_classes_after_standing_pass():
    """Use real YoloE.detect with a CPU-only model stub; no weights are loaded."""
    class Model:
        def __init__(self):
            self.prompts, self.active_classes, self.predict_classes = [], (), []
            self.thresholds = []

        def get_text_pe(self, labels):
            self.prompts.append(tuple(labels))
            return tuple(labels)

        def set_classes(self, labels, embeddings):
            assert tuple(labels) == embeddings
            self.active_classes = tuple(labels)

        def predict(self, **kwargs):
            self.predict_classes.append(self.active_classes)
            self.thresholds.append(kwargs['conf'])
            return [SimpleNamespace(boxes=None)]

    detector = YoloE.__new__(YoloE)
    detector.model, detector.device = Model(), 'cpu'
    detector.info_model, detector.current, detector.embeddings = 'stub-yoloe.pt', None, OrderedDict()
    adapter = YoloStanding(detector)
    generic = Options(prompt='person, laptop', confidence=.7)
    image = np.zeros((16, 16, 3), np.uint8)
    detector.detect(image, generic)
    adapter.estimate(image, [person()], [person(None)], 1000, confidence=.15)
    detector.detect(image, generic)
    assert detector.model.predict_classes == [('person', 'laptop'), ('standing person',), ('person', 'laptop')]
    assert detector.model.prompts == [('person', 'laptop'), ('standing person',)]
    assert detector.model.thresholds == [.7, .15, .7]
    assert generic.prompt == 'person, laptop' and generic.confidence == .7
