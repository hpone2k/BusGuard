import cv2
import numpy as np
import pytest

from vision.schema import Detection, Options
from vision.tracking import StableTracker, OneEuro
from vision.jobs import boxes_at_time


def box(x=.2, score=.8, label='person'):
    return Detection(label, [x, .2, x + .15, .5], score)


def test_one_frame_false_positive_never_becomes_visible():
    tracker = StableTracker()
    assert tracker.update([box()], 0) == []
    assert tracker.update([], .033) == []
    assert tracker.update([], .067) == []


def test_two_of_three_confirmation_and_low_confidence_continuation():
    tracker = StableTracker()
    assert tracker.update([box()], 0) == []
    assert tracker.update([], .033) == []
    confirmed = tracker.update([box()], .067)[0]
    weak = tracker.update([box(.201, .2), box(.6, .2)], .1)
    assert len(weak) == 1 and weak[0].track_id == confirmed.track_id
    assert weak[0].score == .2 and not weak[0].predicted
    assert tracker.detection_options.confidence < tracker.options.confidence


def test_ids_are_isolated_when_other_sessions_and_labels_start():
    a, b = StableTracker(), StableTracker()
    a.update([box()], 0)
    first = a.update([box()], .033)[0]
    b.update([box(label='dog')], 0)
    b.update([box(label='dog')], .033)
    a.update([box(), box(.6, label='dog')], .067)
    detections = a.update([box(), box(.6, label='dog'), box(.7)], .1)
    assert next(d for d in detections if d.label == 'person').track_id == first.track_id
    detections = a.update([box(), box(.6, label='dog'), box(.7)], .133)
    assert len(detections) == 3
    assert len({d.track_id for d in detections}) == 3


def test_filter_reduces_stationary_jitter_and_resets_after_a_gap():
    rng = np.random.default_rng(4479)
    tracker = StableTracker()
    raw, filtered = [], []
    for i in range(100):
        x = .2 + rng.normal(0, .004)
        detections = tracker.update([box(x)], i / 30)
        if i > 10:
            raw.append(x); filtered.append(detections[0].bbox[0])
    assert np.std(filtered) < np.std(raw) * .7
    assert tracker.update([box(.6)], 10) == []
    assert tracker.update([box(.6)], 10.033)[0].bbox[0] == pytest.approx(.6)


def test_prediction_expires_by_source_time_and_does_not_invent_scores():
    tracker = StableTracker()
    tracker.update([box(score=None)], 0)
    measured = tracker.update([box(.202, score=None)], .033)[0]
    held = tracker.update([], .1)[0]
    assert held.predicted and held.score is None
    assert held.observed_at_ms == measured.observed_at_ms
    assert tracker.update([], .2) == []
    assert tracker.update([box()], .1) == []  # Out-of-order timestamp.


def test_responsive_mode_tracks_fast_motion_with_less_filter_lag():
    filters = [OneEuro(2.5, 8), OneEuro(5, 12)]
    outputs = []
    for filt in filters:
        for i in range(20):
            value = np.array([.2 + i * .004, .3, .2, .2])
            result = filt.update(value, i / 30)
        outputs.append(abs(result[0] - value[0]))
    assert outputs[1] < outputs[0]


def test_raw_mode_has_no_confirmation_or_smoothing():
    tracker = StableTracker(Options(stabilization='off'))
    assert tracker.update([box()], 0)[0].bbox == box().bbox
    assert tracker.update([], .033) == []
    assert tracker.detection_options.confidence == .35


def test_camera_motion_recovers_an_object_after_a_large_camera_shift():
    rng = np.random.default_rng(7)
    original = rng.integers(0, 255, (360, 640, 3), dtype=np.uint8)
    tracker = StableTracker()
    d = Detection('object', [.4, .4, .43, .5], .9)
    tracker.update([d], 0, original)
    track_id = tracker.update([d], 1/30, original)[0].track_id
    transform = np.float32([[1, 0, 32], [0, 1, 0]])
    moved = cv2.warpAffine(original, transform, (640, 360))
    d = Detection('object', [.45, .4, .48, .5], .9)
    found = tracker.update([d], 2/30, moved)
    assert any(x.track_id == track_id and not x.predicted for x in found)


def test_export_does_not_extend_a_missing_object_past_prediction_limit():
    detection = {**box().json(), 'predicted': True, 'observed_at_ms': 0}
    assert boxes_at_time([{'time': .1, 'detections': [detection]}], 0, .1)
    assert boxes_at_time([{'time': .1, 'detections': [detection]}], 0, .2) == []


def test_responsive_prediction_deadline_survives_video_export():
    tracker = StableTracker(Options(stabilization='responsive'))
    tracker.update([box()], 0)
    tracker.update([box()], .033)
    predicted = tracker.update([], .067)[0].json()
    assert predicted['prediction_expires_at_ms'] == pytest.approx(133)
    assert boxes_at_time([{'time': .067, 'detections': [predicted]}], 0, .14) == []
