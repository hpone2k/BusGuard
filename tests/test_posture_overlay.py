"""The preview renders semantic posture boxes, never old joint geometry."""
import copy

import numpy as np
import pytest

from vision.media import draw_posture, posture_boxes


def summary():
    return dict(status='observed', engine='LocateAnything', age_ms=1300,
                people=1, standing=1, seated=0, unknown=0,
                occupants=[dict(track_id=1, posture='standing', bbox=[.2, .2, .4, .9])])


def test_semantic_person_box_is_visible_without_changing_accounting(monkeypatch):
    evidence = summary()
    before = copy.deepcopy(evidence)
    text = []
    monkeypatch.setattr('vision.media.cv2.putText', lambda frame, label, *args: text.append(label))
    frame = draw_posture(np.zeros((360, 640, 3), np.uint8), evidence)
    assert frame.any()
    assert text == ['Standing person']
    assert evidence == before


@pytest.mark.parametrize('posture', ['standing', 'seated', 'unknown', 'not_detected', None])
def test_only_standing_boxes_are_visible_not_legacy_sitting_or_joint_diagnostics(monkeypatch, posture):
    evidence = summary()
    evidence['occupants'][0]['posture'] = posture
    text = []
    monkeypatch.setattr('vision.media.cv2.putText', lambda frame, value, *args: text.append(value))
    monkeypatch.setattr('vision.media.cv2.circle', lambda *args: pytest.fail('Joints must not be drawn.'))
    monkeypatch.setattr('vision.media.cv2.line', lambda *args: pytest.fail('Skeleton bones must not be drawn.'))
    frame = draw_posture(np.zeros((360, 640, 3), np.uint8), evidence)
    assert text == (['Standing person'] if posture == 'standing' else [])
    assert bool(frame.any()) is (posture == 'standing')


@pytest.mark.parametrize('status', ['stale', 'paused', 'disabled', 'unavailable', 'awaiting_fresh_frame'])
def test_counter_only_or_unavailable_posture_has_no_semantic_overlay(status):
    evidence = summary()
    evidence['status'] = status
    assert not draw_posture(np.zeros((360, 640, 3), np.uint8), evidence).any()
    assert posture_boxes(evidence) == []


@pytest.mark.parametrize('box', [[], [.1, .1, .4], [float('nan'), .1, .4, .9],
                                [-.1, .1, .4, .9], [.1, .1, 1.1, .9], [.1, .1, True, .9],
                                [.4, .1, .1, .9], ['0.1', .1, .4, .9]])
def test_invalid_boxes_never_extrapolate_a_person(box):
    evidence = summary()
    evidence['occupants'][0]['bbox'] = box
    assert not draw_posture(np.zeros((360, 640, 3), np.uint8), evidence).any()


def test_predicted_and_legacy_joint_rows_do_not_make_semantic_boxes():
    evidence = summary()
    evidence['occupants'][0]['predicted'] = True
    assert posture_boxes(evidence) == []
    points = [dict(x=.4, y=.4, visibility=.9, presence=.9)] * 33
    evidence.update(occupants=[dict(posture='standing', landmarks=points)],
                    unassigned_poses=[dict(landmarks=points)], connections=[[11, 23]])
    assert not draw_posture(np.zeros((360, 640, 3), np.uint8), evidence).any()


@pytest.mark.parametrize('engine,age,visible', [('LocateAnything', 1300, True),
    ('LocateAnything', 3999, True), ('LocateAnything', 4000, False), ('MediaPipe', 1000, False),
    ('YOLOE', 999, True), ('YOLOE', 1000, False),
    (None, 1000, False), ('LocateAnything', float('inf'), False), ('LocateAnything', -1, False)])
def test_only_explicit_locateanything_gets_four_second_preview_lifetime(engine, age, visible):
    evidence = summary()
    evidence.update(engine=engine, age_ms=age)
    assert bool(posture_boxes(evidence)) is visible
