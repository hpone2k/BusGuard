import math

import pytest

import vision.counting as counting
from vision.counting import RollingCountVoter, age_summary, snapshot_counts
from vision.schema import Detection


def detections(count, label='person', **attributes):
    return [Detection(label, [.1, .1, .3, .5], .8, track_id=index, **attributes)
            for index in range(count)]


def establish(voter, count=7, start=0):
    voter.observe(detections(count), start)
    return voter.observe(detections(count), start + 1000)


def test_exact_user_example_is_time_weighted_mode_not_mean_or_frame_vote():
    voter = RollingCountVoter(['person'])
    voter.observe(detections(7), 0)
    voter.observe(detections(6), 700)
    voter.observe(detections(5), 900)
    result = voter.observe(detections(5), 1000)
    assert result['coverage_ms'] == 1000
    assert result['total']['stable'] == 7
    assert result['total']['raw'] == 5
    assert result['total']['support'] == pytest.approx(.7)
    assert result['total']['distribution'] == [
        {'count': 7, 'duration_ms': 700, 'share': .7},
        {'count': 6, 'duration_ms': 200, 'share': .2},
        {'count': 5, 'duration_ms': 100, 'share': .1},
    ]
    assert result['classes']['person'] == result['total']


def test_variable_fps_does_not_outvote_longer_observation_time():
    sparse, dense = RollingCountVoter(), RollingCountVoter()
    for timestamp, count in [(0, 7), (700, 6), (900, 5), (1000, 5)]:
        sparse.observe(detections(count), timestamp)
    for timestamp, count in [(0, 7), (700, 6)] + [(t, 6) for t in range(710, 900, 10)] + [(900, 5), (1000, 5)]:
        dense.observe(detections(count), timestamp)
    assert sparse.snapshot()['total'] == dense.snapshot()['total']


def test_full_window_is_required_and_snapshots_do_not_create_votes():
    voter = RollingCountVoter(['person'])
    voter.observe(detections(7), 0)
    result = voter.observe(detections(7), 999)
    assert result['status'] == 'warming' and result['total']['stable'] is None
    for now in [1000, 1100, 1800]:
        snapshot = voter.snapshot(now)
        assert snapshot['total'] == result['total']
        assert snapshot['coverage_ms'] == 999
    result = voter.observe(detections(7), 1000)
    assert result['status'] == 'stable' and result['total']['stable'] == 7


def test_snapshot_cannot_gain_support_by_aging_out_a_competing_vote():
    voter = RollingCountVoter()
    for timestamp, count in [(0, 6), (400, 7), (1000, 7)]:
        result = voter.observe(detections(count), timestamp)
    assert result['status'] == 'uncertain' and result['total']['support'] == .6
    assert voter.snapshot(1450)['total'] == result['total']


def test_predicted_boxes_are_excluded_and_ids_deduplicated_within_class():
    voter = RollingCountVoter(['Person', 'laptop', 'wheelchair'])
    batch = detections(2) + detections(1) + detections(3, predicted=True) + detections(1, 'laptop')
    voter.observe(batch, 0)
    result = voter.observe([item.json() for item in batch], 1000)
    assert result['total']['stable'] == 3
    assert result['classes']['person']['stable'] == 2
    assert result['classes']['laptop']['stable'] == 1
    assert result['classes']['wheelchair']['stable'] == 0


def test_zero_is_a_real_observation_and_must_win_the_window():
    voter = RollingCountVoter(['person'])
    establish(voter)
    assert voter.observe([], 1100)['total']['stable'] == 7
    assert voter.observe([], 1450)['total']['stable'] == 7
    result = voter.observe([], 2100)
    assert result['status'] == 'stable' and result['total']['stable'] == 0
    assert result['classes']['person']['stable'] == 0


def test_tie_is_uncertain_and_previous_count_is_held_briefly_only():
    voter = RollingCountVoter()
    establish(voter)
    voter.observe(detections(6), 1100)
    result = voter.observe(detections(7), 1600)
    assert result['total']['candidate'] is None
    assert result['total']['support'] == .5
    assert result['status'] == 'uncertain' and result['total']['stable'] == 7
    assert result['total']['held_age_ms'] == 500
    assert result['total']['held_remaining_ms'] == 500
    expired = voter.snapshot(2101)
    assert expired['status'] == 'uncertain' and expired['total']['stable'] is None
    assert expired['total']['candidate'] is None


def test_stale_data_returns_unknown_not_zero_and_does_not_mutate_last_vote():
    voter = RollingCountVoter(['person'])
    established = establish(voter)
    stale = voter.snapshot(2500)
    assert stale['status'] == 'stale' and stale['age_ms'] == 1500
    for estimate in [stale['total'], stale['classes']['person']]:
        assert estimate['raw'] is estimate['stable'] is estimate['candidate'] is None
        assert estimate['support'] is None and estimate['distribution'] == []
    assert voter.snapshot()['total'] == established['total']


def test_long_gap_resets_continuity_and_does_not_create_fake_votes():
    voter = RollingCountVoter(['person'])
    establish(voter)
    result = voter.observe(detections(6), 2501)
    assert result['status'] == 'warming' and result['coverage_ms'] == 0
    assert result['total']['raw'] == 6 and result['total']['stable'] is None
    result = voter.observe(detections(6), 3501)
    assert result['status'] == 'stable' and result['total']['stable'] == 6


def test_out_of_order_and_duplicate_frames_do_not_change_evidence():
    voter = RollingCountVoter()
    established = establish(voter)
    assert voter.observe(detections(100), 900) == established
    assert voter.observe(detections(100), 1000) == established


def test_rolling_window_replaces_old_count_after_sustained_real_change():
    voter = RollingCountVoter()
    establish(voter)
    voter.observe(detections(6), 1100)
    voter.observe(detections(6), 1700)
    result = voter.observe(detections(6), 1750)
    assert result['total']['stable'] == 6 and result['total']['support'] == .65
    result = voter.observe(detections(6), 2100)
    assert result['coverage_ms'] == 1000 and result['total']['support'] == 1
    assert result['total']['distribution'] == [{'count': 6, 'duration_ms': 1000, 'share': 1}]


def test_still_image_is_explicitly_instantaneous_without_temporal_support():
    result = snapshot_counts(detections(3), ['person', 'laptop'])
    assert result['status'] == 'instant' and result['total']['stable'] == 3
    assert result['total']['support'] is None and result['coverage_ms'] == 0
    assert result['classes']['laptop']['stable'] == 0


def test_independent_voters_do_not_share_evidence_or_return_mutable_internal_state():
    inside, outside = RollingCountVoter(), RollingCountVoter()
    result = establish(inside, 7)
    establish(outside, 3)
    result['total']['stable'] = 999
    result['classes'].clear()
    assert inside.snapshot()['total']['stable'] == 7
    assert outside.snapshot()['total']['stable'] == 3


@pytest.mark.parametrize('value', [math.nan, math.inf, -math.inf])
def test_invalid_timestamps_are_rejected(value):
    voter = RollingCountVoter()
    with pytest.raises(ValueError):
        voter.observe([], value)


def test_fractional_milliseconds_accept_full_window_with_float_roundoff():
    voter = RollingCountVoter()
    for frame in range(31):
        result = voter.observe(detections(4), 100.1 + frame * (1000 / 30))
    assert result['status'] == 'stable' and result['total']['stable'] == 4


def test_aging_frozen_summary_expires_hold_without_repeated_packet_extension():
    voter = RollingCountVoter()
    establish(voter)
    voter.observe(detections(6), 1100)
    result = voter.observe(detections(7), 1600)
    halfway = age_summary(result, 300)
    assert halfway['total']['stable'] == 7
    assert halfway['total']['held_age_ms'] == 800
    assert halfway['total']['held_remaining_ms'] == 200
    assert age_summary(halfway, 300) == halfway
    expired = age_summary(halfway, 501)
    assert expired['total']['stable'] is None
    assert expired['total']['held_age_ms'] == 1001
    assert expired['total']['held_remaining_ms'] == 0
    assert expired['total']['distribution'] == result['total']['distribution']
    assert age_summary(result, 1500)['status'] == 'stale'


def test_every_uncertain_observation_carries_time_since_last_confirmation():
    voter = RollingCountVoter()
    establish(voter)
    voter.observe(detections(6), 1100)
    first = voter.observe(detections(7), 1600)
    later = voter.observe(detections(6), 1700)
    assert first['total']['held_age_ms'] == 500
    assert later['total']['held_age_ms'] == 600
    assert later['total']['held_remaining_ms'] == 400


def test_total_is_voted_independently_from_each_class():
    voter = RollingCountVoter(['person', 'laptop'])
    voter.observe(detections(1), 0)
    voter.observe(detections(1, 'laptop'), 400)
    voter.observe(detections(1) + detections(1, 'laptop'), 750)
    result = voter.observe(detections(1) + detections(1, 'laptop'), 1000)
    assert result['total']['stable'] == 1
    assert result['total']['support'] == .75
    assert result['classes']['person']['stable'] == 1
    assert result['classes']['person']['support'] == .65
    assert result['classes']['laptop']['stable'] is None
    assert result['classes']['laptop']['status'] == 'uncertain'


def test_unknown_labels_and_interval_history_are_bounded(monkeypatch):
    voter = RollingCountVoter()
    batch = [Detection('object ' + str(index), [.1, .1, .3, .5], .8) for index in range(300)]
    result = voter.observe(batch, 0)
    assert len(result['classes']) == 64 and result['total']['raw'] == 300
    monkeypatch.setattr(counting, 'MAX_INTERVALS', 4)
    voter = RollingCountVoter()
    for frame in range(11):
        result = voter.observe(detections(frame % 2), frame * 100)
    assert len(voter.intervals) <= 4
    assert result['coverage_ms'] == 400
    assert result['status'] == 'warming'
