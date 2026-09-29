import pytest

from vision.departure import DepartureInterlock


def source(frame, now, postures=('seated', 'seated'), ids=None, **changes):
    ids = ids or list(range(1, len(postures) + 1))
    occupants = [dict(track_id=identifier, posture=posture) for identifier, posture in zip(ids, postures)]
    summary = dict(status='observed', age_ms=0, captured_at_ms=now * 1000,
                   complete=True, people=len(occupants), occupants=occupants,
                   **{posture: postures.count(posture) for posture in ['seated', 'standing', 'unknown']})
    value = dict(kind='live', session_id='inside-1', frame_id=frame, connected=True, is_demo=False,
                 age_ms=0, received_at_ms=now * 1000, seating_summary=summary,
                 count_summary={'classes': {'person': {'status': 'stable', 'stable': len(occupants)}}})
    value.update(changes)
    return value


def update(gate, packet, now, closed=True):
    gate.update(packet, closed=closed, enabled=True, now=now)
    return gate.result(now)


def confirm(gate, first_frame=0, first_time=1000., **changes):
    state = None
    for offset in range(10):
        now = first_time + offset * .4
        state = update(gate, source(first_frame + offset, now, **changes), now)
    return state


def test_three_seconds_of_new_postclosure_frames_can_confirm():
    gate = DepartureInterlock()
    assert confirm(gate)['can_depart']
    assert not gate.result(1005.)['can_depart']


def test_standing_passenger_disappearing_cannot_clear_the_roster():
    gate = DepartureInterlock()
    update(gate, source(0, 1000., ('seated', 'standing')), 1000.)
    for frame in range(1, 15):
        now = 1000. + frame * .4
        state = update(gate, source(frame, now, ('seated',), ids=[1]), now)
    assert not state['can_depart']
    assert state['expected_people'] == 2
    assert 'no longer accounted' in state['message']


def test_roster_survives_disconnect_and_stale_packets():
    gate = DepartureInterlock()
    update(gate, source(0, 1000., ('seated', 'standing')), 1000.)
    update(gate, None, 1000.5)
    update(gate, source(1, 1001., ('seated',), ids=[1], age_ms=2000), 1001.)
    assert not confirm(gate, first_frame=2, first_time=1002., postures=('seated',), ids=[1])['can_depart']
    assert gate.result(1004.4)['expected_people'] == 2


def test_untracked_visible_passenger_cannot_disappear_from_count():
    gate = DepartureInterlock()
    update(gate, source(0, 1000., ('seated', 'unknown'), ids=[1, None]), 1000.)
    assert not confirm(gate, first_frame=1, first_time=1001., postures=('seated',), ids=[1])['can_depart']
    assert gate.result(1003.4)['expected_people'] == 2


def test_only_reopened_door_cycle_clears_missing_passenger_roster():
    gate = DepartureInterlock()
    update(gate, source(0, 1000., ('seated', 'standing')), 1000.)
    update(gate, source(1, 1000.4, ('seated',), ids=[1]), 1000.4)
    update(gate, None, 1001., closed=False)
    assert confirm(gate, first_frame=2, first_time=1002., postures=('seated',), ids=[1])['can_depart']


def test_seating_before_closure_cannot_carry_confirmation_after_closure():
    gate = DepartureInterlock()
    for frame in range(10):
        now = 1000. + frame * .4
        update(gate, source(frame, now), now, closed=False)
    packet = source(9, 1003.6)
    assert not update(gate, packet, 1004.)['can_depart']
    assert not update(gate, packet, 1004.4)['can_depart']
    assert update(gate, source(10, 1004.8), 1004.8)['progress_ms'] == 0
    assert confirm(gate, first_frame=11, first_time=1005.2)['can_depart']


def test_source_change_starts_new_confirmation_and_preserves_minimum_population():
    gate = DepartureInterlock()
    assert confirm(gate)['can_depart']
    packet = source(0, 1004., session_id='inside-2')
    assert not update(gate, packet, 1004.)['can_depart']
    assert gate.result(1004.)['progress_ms'] == 0
    assert not confirm(gate, first_frame=1, first_time=1004.4, session_id='inside-2',
                       postures=('seated',), ids=[10])['can_depart']


@pytest.mark.parametrize('change', ['backward_frame', 'backward_capture', 'same_frame_changed_capture'])
def test_frame_and_capture_order_must_increase(change):
    gate = DepartureInterlock()
    assert confirm(gate)['can_depart']
    packet = source(10, 1004.)
    if change == 'backward_frame':
        packet['frame_id'] = 4
    elif change == 'backward_capture':
        packet['seating_summary']['captured_at_ms'] = 1002. * 1000
    else:
        packet['frame_id'] = 9
    assert not update(gate, packet, 1004.)['can_depart']
    assert gate.result(1004.)['progress_ms'] == 0


def test_repeated_frame_never_grows_confirmation_or_becomes_younger():
    gate = DepartureInterlock()
    update(gate, source(0, 1000.), 1000.)
    packet = source(1, 1000.4)
    for offset in range(12):
        state = update(gate, packet, 1000.4 + offset * .2)
    assert not state['can_depart']
    assert state['progress_ms'] == 0


@pytest.mark.parametrize('kind', ['image', 'video'])
def test_test_inputs_cannot_establish_cabin_posture(kind):
    gate = DepartureInterlock()
    assert not confirm(gate, kind=kind)['can_depart']


@pytest.mark.parametrize('corrupt', ['duplicate_track', 'count_mismatch', 'unknown_track', 'missing_count', 'warming_count', 'capacity_reached'])
def test_inconsistent_occupants_and_counts_remain_held(corrupt):
    gate = DepartureInterlock()
    for frame in range(8):
        now = 1000. + frame * .4
        packet = source(frame, now)
        if corrupt == 'duplicate_track':
            packet['seating_summary']['occupants'][1]['track_id'] = 1
        elif corrupt == 'count_mismatch':
            packet['seating_summary']['seated'] = 1
        elif corrupt == 'unknown_track':
            packet['seating_summary']['occupants'][1]['track_id'] = None
        elif corrupt == 'missing_count':
            packet['count_summary'] = None
        elif corrupt == 'capacity_reached':
            packet['seating_summary']['capacity_reached'] = True
        else:
            packet['count_summary']['classes']['person']['status'] = 'warming'
        state = update(gate, packet, now)
    assert not state['can_depart']


def test_fast_forward_capture_timestamps_cannot_manufacture_confirmation_time():
    gate = DepartureInterlock()
    for frame in range(10):
        now = 1000. + frame * .05
        packet = source(frame, now)
        packet['seating_summary']['captured_at_ms'] = 1000000 + frame * 800
        state = update(gate, packet, now)
    assert not state['can_depart']
    assert state['progress_ms'] < 500


def test_large_gap_restarts_confirmation():
    gate = DepartureInterlock()
    assert confirm(gate)['can_depart']
    state = update(gate, source(10, 1005.), 1005.)
    assert not state['can_depart']
    assert state['progress_ms'] == 0


def test_standing_interrupts_entire_three_second_confirmation():
    gate = DepartureInterlock()
    for frame in range(8):
        now = 1000. + frame * .4
        state = update(gate, source(frame, now), now)
    assert state['progress_ms'] == 2400 and not state['can_depart']
    assert update(gate, source(8, 1003.2, ('standing', 'seated')), 1003.2)['progress_ms'] == 0
    for frame in range(9, 17):
        now = 1000. + frame * .4
        state = update(gate, source(frame, now), now)
        assert not state['can_depart']
    assert update(gate, source(17, 1006.8), 1006.8)['can_depart']


@pytest.mark.parametrize('coverage,expected,ready', [(False, 0, False), (True, 0, True), (True, 1, False)])
def test_zero_person_confirmation_requires_whole_cabin_and_no_known_occupants(coverage, expected, ready):
    gate = DepartureInterlock()
    for frame in range(10):
        now = 1000. + frame * .4
        gate.update(source(frame, now, ()), closed=True, enabled=True, now=now,
                    expected_people=expected, allow_empty=coverage)
    assert gate.result(now)['can_depart'] is ready
