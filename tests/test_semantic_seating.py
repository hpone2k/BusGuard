import copy

import pytest

from vision.schema import Detection
from vision.semantic_seating import estimate


BOX = [.1, .1, .4, .9]
OTHER = [.6, .1, .9, .9]


def person(identifier=1, box=BOX, **changes):
    return Detection('person', list(box), .9, track_id=identifier, **changes)


def posture(label='sitting person', box=BOX):
    return Detection(label, list(box))


@pytest.mark.parametrize('label,expected', [('standing person', 'standing'), ('sitting person', 'seated'),
                                         (' seated person ', 'seated'), ('PERSON STANDING', 'standing')])
def test_unique_same_frame_semantic_label_matches_tracked_person(label, expected):
    summary = estimate([person()], [person(None)], [posture(label)], 123)
    assert summary[expected] == summary['people'] == summary['raw_person_count'] == 1
    assert summary['complete'] and summary['unknown'] == 0
    assert summary['engine'] == 'LocateAnything'
    assert summary['occupants'][0]['bbox'] == BOX
    assert summary['occupants'][0]['track_id'] == 1
    assert 'landmarks' not in summary['occupants'][0]
    assert summary['scores_available'] is False


def test_standing_and_sitting_are_separate_from_raw_person_count():
    tracked = [person(), person(2, OTHER)]
    summary = estimate(tracked, tracked, [posture(), posture('standing person', OTHER)], 123)
    assert summary['people'] == summary['raw_person_count'] == 2
    assert summary['seated'] == summary['standing'] == 1
    assert summary['complete']


def test_conflicting_posture_boxes_are_one_unknown_person_not_two_people():
    summary = estimate([person()], [person(None)], [posture(), posture('standing person')], 123)
    assert summary['people'] == summary['unknown'] == summary['raw_person_count'] == 1
    assert summary['standing'] == summary['seated'] == 0 and not summary['complete']
    assert 'conflicting' in summary['occupants'][0]['reason']


def test_same_class_near_identical_boxes_do_not_create_artificial_ambiguity():
    duplicate = posture('seated person', [.1001, .1, .4001, .9])
    summary = estimate([person()], [person(None)], [posture(), duplicate], 123)
    assert summary['complete'] and summary['seated'] == 1
    assert summary['semantic_detection_count'] == 1


def test_missing_raw_or_predicted_track_cannot_grant_posture_clearance():
    for tracked, raw in (([person()], []), ([person(predicted=True)], [person(None)]),
                         ([person(None)], [person(None)]), ([person(True)], [person(None)])):
        summary = estimate(tracked, raw, [posture()], 123)
        assert summary['seated'] == 0 and summary['unknown'] > 0 and not summary['complete']


def test_duplicate_track_identity_and_crowded_matching_remain_unknown():
    tracked = [person(), person(1, OTHER)]
    summary = estimate(tracked, tracked, [posture(), posture(box=OTHER)], 123)
    assert summary['seated'] == 0 and summary['unknown'] == 2 and not summary['complete']
    crowded = [person(), person(2)]
    summary = estimate(crowded, crowded, [posture()], 123)
    assert summary['seated'] == 0 and summary['unknown'] > 0 and not summary['complete']


def test_untracked_newcomer_with_semantic_label_is_unknown_once():
    summary = estimate([], [person(None)], [posture()], 123)
    assert summary['people'] == summary['unknown'] == 1
    assert summary['seated'] == 0 and not summary['complete']


def test_unmatched_semantic_person_keeps_known_seated_subset_from_clearing_departure():
    summary = estimate([person()], [person(None)], [posture(), posture('standing person', OTHER)], 123)
    assert summary['raw_person_count'] == 1
    assert summary['people'] == 2 and summary['unknown'] == summary['seated'] == 1
    assert not summary['complete']
    assert summary['occupants'][1]['track_id'] is None


def test_semantic_only_conflicting_extra_counts_once_and_never_asserts_seated():
    summary = estimate([], [], [posture(), posture('standing person')], 123)
    assert summary['people'] == summary['unknown'] == 1
    assert summary['raw_person_count'] == summary['seated'] == summary['standing'] == 0
    assert not summary['complete']


def test_unavailable_model_keeps_person_roster_unknown_and_drops_semantic_extras():
    summary = estimate([person()], [person(None)], [posture(), posture(box=OTHER)], 123,
                       available=False, unavailable_reason='Model is loading.')
    assert summary['status'] == 'unavailable' and summary['reason'] == 'Model is loading.'
    assert summary['people'] == summary['unknown'] == 1 and summary['seated'] == 0
    assert not summary['complete']


@pytest.mark.parametrize('box', [[float('nan'), .1, .4, .9], [-.1, .1, .4, .9],
                               [.4, .1, .1, .9], [True, .1, .4, .9]])
def test_invalid_classified_coordinates_cannot_assert_seating(box):
    summary = estimate([person()], [person(None)], [posture(box=box)], 123)
    assert summary['unknown'] == 1 and summary['seated'] == 0 and not summary['complete']


def test_arbitrary_labels_and_no_standing_detection_never_imply_everyone_seated():
    for detections in ([], [posture('person')], [posture('not a standing person')]):
        summary = estimate([person()], [person(None)], detections, 123)
        assert summary['unknown'] == 1 and summary['seated'] == 0 and not summary['complete']


def test_inputs_are_not_mutated_and_empty_frame_is_not_seated_confirmation():
    tracked, raw, detections = [person().json()], [person(None).json()], [posture().json()]
    before = copy.deepcopy((tracked, raw, detections))
    estimate(tracked, raw, detections, 123)
    assert (tracked, raw, detections) == before
    summary = estimate([], [], [], 123)
    assert summary['people'] == 0 and not summary['complete']


def test_malformed_inputs_are_unavailable_and_overflow_never_clears_departure():
    assert estimate([], [], None, 123)['status'] == 'unavailable'
    summary = estimate([person(i) for i in range(301)], [], [], 123)
    assert summary['capacity_reached'] and not summary['complete']
    assert len(summary['occupants']) <= 300
