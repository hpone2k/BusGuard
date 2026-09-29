from dataclasses import FrozenInstanceError

import pytest

from vision.cabin_audit import CabinAudit


def source(count, frame, now, **changes):
    packet = dict(kind='live', connected=True, is_demo=False, prompt_mode='objects',
                  session_id='inside-1', frame_id=frame, age_ms=0,
                  received_at_ms=now * 1000, count_summary=dict(
                      age_ms=0, last_observed_ms=now * 1000, coverage=1, classes={
                          'person': dict(stable=count, support=.8, status='stable')}))
    packet.update(changes)
    return packet


def cycle(audit, counts, start=100, frame=0, **changes):
    for second, count in enumerate(counts):
        now = start + second
        assert audit.observe(source(count, frame + second, now, **changes), now) is None
    now = start + len(counts)
    return audit.observe(source(counts[-1], frame + len(counts), now, **changes), now)


def test_exact_thirty_second_window_repeats_and_emits_once():
    audit = CabinAudit()
    for second in range(30):
        now = 100.25 + second
        assert audit.observe(source(7, second, now), now) is None
    assert audit.snapshot(129.25)['collected'] == 30
    assert audit.snapshot(129.25)['remaining_seconds'] == 1
    assert audit.result is None
    result = audit.observe(source(7, 30, 130.25), 130.25)
    assert result.mean == result.rounded == 7
    assert result.completed_at - result.started_at == 30
    assert result.samples == 30 and result.revision == 1
    assert audit.snapshot(130.25)['collected'] == 1
    assert audit.observe(source(7, 30, 130.25), 130.25) is None
    for second in range(1, 30):
        now = 130.25 + second
        assert audit.observe(source(7, 30 + second, now), now) is None
    result2 = audit.observe(source(7, 60, 160.25), 160.25)
    assert result2.revision == 2
    with pytest.raises(FrozenInstanceError):
        result.mean = 99


@pytest.mark.parametrize('counts,expected_mean,expected_round', [
    ([0] * 30, 0, 0), ([6] * 15 + [7] * 15, 6.5, 7),
    ([6] * 16 + [7] * 14, 194 / 30, 6), ([300] * 30, 300, 300),
])
def test_arithmetic_mean_and_half_up_rounding(counts, expected_mean, expected_round):
    result = cycle(CabinAudit(), counts)
    assert result.mean == pytest.approx(expected_mean)
    assert result.rounded == expected_round


def test_variable_frame_rate_has_one_equal_weight_latest_sample_per_second():
    audit = CabinAudit()
    frame = 0
    for second in range(30):
        # First fifteen seconds: thirty frames of 2, last value 4.
        # Last fifteen seconds: only two frames, last value 8.
        fps = 30 if second < 15 else 2
        for index in range(fps):
            now = 100 + second + index / fps
            count = (4 if second < 15 else 8) if index == fps - 1 else 2
            assert audit.observe(source(count, frame, now), now) is None
            frame += 1
    result = audit.observe(source(99, frame, 130), 130)
    assert result.mean == 6 and result.rounded == 6


def test_duplicate_polling_never_supplies_another_second():
    audit = CabinAudit()
    packet = source(8, 1, 100)
    audit.observe(packet, 100)
    for tenth in range(1, 10):
        assert audit.observe(packet, 100 + tenth / 10) is None
    assert audit.snapshot(100.9)['collected'] == 1
    # Even forged fresh receipt/age values cannot refresh the same frame.
    for second in range(1, 35):
        changed = {**packet, 'received_at_ms': (100 + second) * 1000}
        assert audit.observe(changed, 100 + second) is None
    assert audit.result is None
    assert audit.snapshot(134)['collected'] == 0


def test_fresh_duplicate_can_finish_cycle_but_does_not_seed_next_cycle():
    audit = CabinAudit()
    for second in range(30):
        audit.observe(source(4, 2 * second, 100 + second), 100 + second)
        packet = source(4, 2 * second + 1, 100 + second + .8)
        audit.observe(packet, 100 + second + .8)
    result = audit.observe(packet, 130)
    assert result.mean == 4
    assert audit.snapshot(130)['collected'] == 0
    assert audit.observe(packet, 130.1) is None
    assert audit.observe(source(4, 60, 130.2), 130.2) is None
    assert audit.snapshot(130.2)['collected'] == 1


def test_missing_second_abandons_cycle_without_fabricated_catchup():
    audit = CabinAudit()
    for second in range(15):
        audit.observe(source(8, second, 100 + second), 100 + second)
    audit.observe(source(8, 17, 117), 117)
    assert audit.snapshot(117)['collected'] == 1
    for second in range(18, 46):
        assert audit.observe(source(8, second, 100 + second), 100 + second) is None
    assert audit.result is None
    assert audit.observe(source(8, 46, 146), 146) is None
    result = audit.observe(source(8, 47, 147), 147)
    assert result.mean == 8 and result.started_at == 117


@pytest.mark.parametrize('changes', [
    {'connected': False}, {'kind': 'image'}, {'kind': 'video'}, {'is_demo': True},
    {'prompt_mode': 'phrase'}, {'age_ms': 1000}, {'age_ms': -1},
    {'received_at_ms': 1}, {'frame_id': True}, {'frame_id': -1},
    {'session_id': None}, {'count_summary': []},
])
def test_invalid_source_never_becomes_zero_or_completes_window(changes):
    audit = CabinAudit()
    for second in range(30):
        audit.observe(source(8, second, 100 + second), 100 + second)
    assert audit.observe(source(8, 30, 130, **changes), 130) is None
    assert audit.result is None
    assert audit.snapshot(130)['collected'] == 0


@pytest.mark.parametrize('field,value', [
    ('age_ms', 1000), ('coverage', .64), ('coverage', float('nan')),
    ('last_observed_ms', None), ('last_observed_ms', float('inf')),
])
def test_stale_or_invalid_count_summary_is_not_a_sample(field, value):
    audit = CabinAudit()
    packet = source(8, 1, 100)
    packet['count_summary'][field] = value
    assert audit.observe(packet, 100) is None
    assert audit.snapshot(100)['collected'] == 0


@pytest.mark.parametrize('field,value', [
    ('status', 'held'), ('status', 'uncertain'), ('support', .64),
    ('stable', True), ('stable', 2.5), ('stable', -1), ('stable', 301),
])
def test_ambiguous_person_count_cannot_enter_mean(field, value):
    audit = CabinAudit()
    packet = source(8, 1, 100)
    packet['count_summary']['classes']['person'][field] = value
    assert audit.observe(packet, 100) is None
    assert audit.snapshot(100)['collected'] == 0


def test_person_subsets_or_overlapping_aliases_are_not_total_occupancy():
    audit = CabinAudit()
    packet = source(8, 1, 100)
    packet['count_summary']['classes']['persons'] = dict(stable=5, status='stable', support=.8)
    assert audit.observe(packet, 100) is None
    packet['count_summary']['classes'] = {
        'person in a red shirt': dict(stable=5, status='stable', support=.8)}
    assert audit.observe(packet, 100) is None
    assert audit.snapshot(100)['collected'] == 0


def test_whole_cabin_is_required_and_change_discards_partial_cycle():
    audit = CabinAudit()
    audit.observe(source(8, 1, 100), 100)
    audit.observe(source(8, 2, 101), 101, whole_cabin=False)
    assert audit.snapshot(101)['collected'] == 0
    audit.observe(source(8, 3, 102), 102, whole_cabin=True)
    assert audit.snapshot(102)['collected'] == 1


def test_disconnection_reconnection_and_source_replacement_start_new_cycles():
    audit = CabinAudit()
    audit.observe(source(8, 1, 100), 100)
    audit.observe(source(8, 2, 101), 101)
    audit.observe({}, 101.1)
    assert audit.snapshot(101.1)['collected'] == 0
    audit.observe(source(8, 3, 101.2), 101.2)
    assert audit.snapshot(101.2)['collected'] == 1
    audit.observe(source(8, 4, 102.2), 102.2)
    audit.observe(source(8, 1, 102.3, session_id='replacement'), 102.3)
    assert audit.snapshot(102.3)['collected'] == 1
    assert audit.snapshot(102.3)['source_id'] == 'replacement'


@pytest.mark.parametrize('frame,capture', [(0, 101000), (1, 101000), (2, 100000), (2, 99999)])
def test_reordered_frames_or_capture_timestamps_abandon_partial_cycle(frame, capture):
    audit = CabinAudit()
    audit.observe(source(8, 1, 100), 100)
    packet = source(8, frame, 100.1)
    packet['count_summary']['last_observed_ms'] = capture
    assert audit.observe(packet, 100.1) is None
    assert audit.snapshot(100.1)['collected'] == 0


def test_last_result_remains_historical_on_disconnect_but_partial_state_is_not_restored():
    audit = CabinAudit()
    completed = cycle(audit, [8] * 30)
    audit.observe({}, 130.1)
    snapshot = audit.snapshot(130.1)
    assert snapshot['last_mean'] == snapshot['last_rounded'] == 8
    assert snapshot['revision'] == completed.revision
    assert snapshot['status'] == 'unavailable' and snapshot['collected'] == 0
    fresh = CabinAudit()
    assert fresh.snapshot(131)['last_mean'] is None
    assert fresh.snapshot(131)['collected'] == 0


def test_silent_gap_at_completion_does_not_apply_old_window():
    audit = CabinAudit()
    for second in range(30):
        audit.observe(source(8, second, 100 + second), 100 + second)
    assert audit.observe(source(3, 50, 150), 150) is None
    assert audit.result is None
    assert audit.snapshot(150)['collected'] == 1


def test_fractional_boundaries_avoid_floating_point_lost_bucket():
    audit = CabinAudit()
    start = 1760000000.375
    result = cycle(audit, [7] * 30, start=start)
    assert result.samples == 30 and result.rounded == 7


def test_explicit_load_reset_discards_partial_and_result_without_reusing_revision():
    audit = CabinAudit()
    cycle(audit, [8] * 30)
    audit.reset()
    snapshot = audit.snapshot(131)
    assert snapshot['last_mean'] is None and snapshot['collected'] == 0
    assert snapshot['source_id'] is None and snapshot['cycle_completed_at'] is None
    assert snapshot['revision'] == 1
    next_result = cycle(audit, [3] * 30, start=131)
    assert next_result.revision == 2 and next_result.rounded == 3


def test_one_hertz_jitter_keeps_complete_one_second_buckets():
    audit = CabinAudit()
    for second in range(30):
        now = 100 + second + (.2 if second % 2 else 0)
        assert audit.observe(source(7, second, now), now) is None
    result = audit.observe(source(7, 30, 130.1), 130.1)
    assert result.mean == 7 and result.completed_at == 130


def test_latest_new_frame_cannot_finish_window_with_stale_final_representative():
    audit = CabinAudit()
    for second in range(29):
        audit.observe(source(8, second, 100 + second), 100 + second)
    packet = source(8, 29, 129.1, age_ms=900)
    packet['count_summary']['age_ms'] = 900
    assert audit.observe(packet, 129.1) is None
    assert audit.snapshot(129.1)['collected'] == 30
    assert audit.observe(source(8, 30, 130), 130) is None
    assert audit.result is None
    assert audit.snapshot(130)['collected'] == 1


def test_window_cannot_be_completed_after_an_entire_unobserved_following_second():
    audit = CabinAudit()
    for second in range(30):
        audit.observe(source(8, second, 100 + second), 100 + second)
    audit.observe(source(8, 30, 129.9), 129.9)
    assert audit.observe(source(8, 31, 131.1), 131.1) is None
    assert audit.result is None
    assert audit.snapshot(131.1)['collected'] == 1
