"""Standing-only results may clear the simulation, never claim seated posture."""
import copy

import pytest

from vision.assistance import AssistanceController
from vision.departure import DepartureInterlock
from vision.schema import Detection
from vision.standing import estimate


BOX = [.1, .1, .4, .9]
OTHER = [.6, .1, .9, .9]


def person(identifier=1, box=BOX, predicted=False):
    return Detection('person', list(box), .9, track_id=identifier, predicted=predicted)


def packet(frame, now, state='clear', *, people=None, **changes):
    tracked = list(people) if people is not None else [person(predicted=state == 'unknown')]
    raw = [person(row.track_id, row.bbox) for row in tracked]
    positives = [Detection('standing person', BOX, .8)] if state == 'standing' else []
    summary = estimate(tracked, raw, positives, now * 1000, available=state != 'unavailable')
    value = dict(kind='live', connected=True, is_demo=False, session_id='inside', frame_id=frame,
                 received_at_ms=now * 1000, age_ms=0, posture_enabled=True, prompt_mode='objects',
                 seating_summary=summary,
                 count_summary=dict(age_ms=0, coverage=1,
                                    classes={'person': dict(stable=len(raw), status='stable', support=1)}))
    value.update(changes)
    return value


def update(gate, source, now, **kwargs):
    gate.update(source, now=now, enabled=True, closed=kwargs.pop('closed', True), **kwargs)
    return gate.result(now)


def confirm(gate, state='clear', start=1000., first_frame=0, **kwargs):
    for offset in range(15):
        now = start + offset * .4
        result = update(gate, packet(first_frame + offset, now, state, **kwargs), now)
    return result


def test_actual_standing_only_negative_confirms_without_classifying_sitting():
    evidence = packet(0, 1000.)['seating_summary']
    assert evidence['seated'] is None
    assert evidence['occupants'][0]['posture'] == 'not_detected'
    assert evidence['clear'] and evidence['complete']
    gate = DepartureInterlock()
    ready = confirm(gate)
    assert ready['can_depart'] and ready['progress_ms'] == ready['confirmation_ms'] == 5000
    assert ready['mode'] == 'standing_only' and ready['max_age_ms'] == 1000
    assert ready['message'].startswith('No standing detected.')
    assert 'seated' not in ready['message'].lower()


@pytest.mark.parametrize('state', ['standing', 'unavailable'])
def test_standing_or_failed_inference_never_clears(state):
    gate = DepartureInterlock()
    held = confirm(gate, state)
    assert not held['can_depart'] and held['progress_ms'] == 0
    assert held['status'] == ('standing' if state == 'standing' else 'unknown')


@pytest.mark.parametrize('corruption', ['clear', 'standing_check_valid', 'missing_validity', 'seated_claim',
                                        'stale', 'disconnected', 'capacity_reached', 'malformed_occupants',
                                        'malformed_posture', 'malformed_count', 'missing_detection_count',
                                        'missing_summary'])
def test_negative_requires_valid_fresh_standing_inference(corruption):
    gate = DepartureInterlock()
    for frame in range(20):
        now = 1000 + .4 * frame
        source = packet(frame, now)
        if corruption in {'clear', 'standing_check_valid'}:
            source['seating_summary'][corruption] = False
        elif corruption == 'missing_validity':
            source['seating_summary'].pop('standing_check_valid')
        elif corruption == 'seated_claim':
            source['seating_summary']['seated'] = 1
        elif corruption == 'stale':
            source['seating_summary']['age_ms'] = 1000
        elif corruption == 'capacity_reached':
            source['seating_summary']['capacity_reached'] = True
        elif corruption == 'malformed_occupants':
            source['seating_summary']['occupants'] = [None]
        elif corruption == 'malformed_posture':
            source['seating_summary']['occupants'][0]['posture'] = []
        elif corruption == 'malformed_count':
            source['seating_summary']['standing'] = False
        elif corruption == 'missing_detection_count':
            source['seating_summary'].pop('standing_detection_count')
        elif corruption == 'missing_summary':
            source['seating_summary'] = None
        else:
            source['connected'] = False
        held = update(gate, source, now)
    assert not held['can_depart'] and held['progress_ms'] == 0


def test_previous_standing_passenger_does_not_create_a_seated_roster_requirement():
    gate = DepartureInterlock()
    update(gate, packet(0, 1000, 'standing', people=[person(), person(2, OTHER)]), 1000)
    ready = confirm(gate, start=1000.4, first_frame=1, people=[person(2, OTHER)])
    assert ready['can_depart'] and ready['expected_people'] == 1


@pytest.mark.parametrize('identities', ['missing', 'changing', 'duplicate'])
@pytest.mark.parametrize('census', ['warming', 'missing', 'mismatched'])
def test_no_standing_clears_despite_unstable_ids_and_mismatched_rfid_or_census(identities, census):
    gate = DepartureInterlock()
    for frame in range(15):
        now = 1000 + frame * .4
        ids = [None, None] if identities == 'missing' else [frame * 2, frame * 2 + 1]
        if identities == 'duplicate':
            ids = [1, 1]
        source = packet(frame, now, people=[person(ids[0]), person(ids[1], OTHER)])
        if census == 'missing':
            source['count_summary'] = None
        else:
            source['count_summary']['classes']['person'].update(
                status='warming' if census == 'warming' else 'stable', stable=18)
        result = update(gate, source, now, expected_people=18)
        if frame < 14:
            assert not result['can_depart']
    assert result['can_depart'] and result['progress_ms'] == 5000
    assert not gate.expected and result['expected_people'] == 2


def test_repeated_negative_frame_never_builds_five_seconds_or_refreshes_age():
    gate = DepartureInterlock()
    update(gate, packet(0, 1000), 1000)
    source = packet(1, 1000.4)
    for offset in range(20):
        held = update(gate, copy.deepcopy(source), 1000.4 + offset * .2)
        assert not held['can_depart']
    assert held['progress_ms'] == 0


def test_standing_interrupts_negative_confirmation_and_requires_five_new_seconds():
    gate = DepartureInterlock()
    for frame in range(8):
        held = update(gate, packet(frame, 1000 + frame * .4), 1000 + frame * .4)
    assert held['progress_ms'] == 2400
    assert update(gate, packet(8, 1003.2, 'standing'), 1003.2)['progress_ms'] == 0
    for frame in range(9, 22):
        now = 1000 + frame * .4
        assert not update(gate, packet(frame, now), now)['can_depart']
    assert update(gate, packet(22, 1008.8), 1008.8)['can_depart']


def test_switching_from_seated_classification_starts_a_new_standing_only_confirmation():
    gate = DepartureInterlock()
    for frame in range(8):
        now = 1000 + frame * .4
        source = packet(frame, now)
        summary = source['seating_summary']
        summary.update(mode='seated', seated=1)
        summary['occupants'][0]['posture'] = 'seated'
        result = update(gate, source, now)
    assert result['progress_ms'] == 2400
    result = update(gate, packet(8, 1003.2), 1003.2)
    assert not result['can_depart'] and result['progress_ms'] == 0
    for frame in range(9, 21):
        now = 1000 + frame * .4
        assert not update(gate, packet(frame, now), now)['can_depart']
    assert update(gate, packet(21, 1008.4), 1008.4)['can_depart']


@pytest.mark.parametrize('coverage,known', [(False, 0), (True, 0), (True, 18)])
def test_successful_zero_standing_pass_is_independent_of_legacy_occupancy(coverage, known):
    gate = DepartureInterlock()
    for frame in range(15):
        now = 1000 + frame * .4
        result = update(gate, packet(frame, now, people=[]), now, allow_empty=coverage, expected_people=known)
    assert result['can_depart']


def test_five_full_seconds_are_required_after_first_postclosure_result():
    gate = DepartureInterlock()
    for frame in range(12):
        now = 1000 + frame * .5
        result = update(gate, packet(frame, now), now)
        assert result['can_depart'] is (frame == 11)
        assert result['progress_ms'] == max(0, frame - 1) * 500


@pytest.mark.parametrize('interruption', ['gap', 'reopen', 'replacement', 'missing', 'standing'])
def test_interrupted_ready_check_requires_new_continuous_confirmation(interruption):
    gate = DepartureInterlock()
    assert confirm(gate)['can_depart']
    now, frame, session = 1006., 15, 'inside'
    if interruption == 'gap':
        now += 2
    elif interruption == 'reopen':
        update(gate, None, now, closed=False)
        now += .4
    elif interruption == 'replacement':
        session = 'replacement'
    elif interruption == 'missing':
        update(gate, None, now)
        now += .4
    else:
        update(gate, packet(frame, now, 'standing'), now)
        now, frame = now + .4, frame + 1
    held = update(gate, packet(frame, now, session_id=session), now)
    assert not held['can_depart'] and held['progress_ms'] == 0
    for offset in range(1, 13):
        when = now + offset * .4
        assert not update(gate, packet(frame + offset, when, session_id=session), when)['can_depart']
    assert confirm(gate, first_frame=frame + 13, start=now + 5.2, session_id=session)['can_depart']


@pytest.mark.parametrize('corruption', ['backward_frame', 'backward_capture', 'same_frame_changed_capture',
                                        'session_mismatch', 'demo', 'image', 'stale'])
def test_invalid_or_old_evidence_revokes_ready(corruption):
    gate = DepartureInterlock()
    assert confirm(gate)['can_depart']
    source = packet(15, 1006.)
    if corruption == 'backward_frame':
        source['frame_id'] = 3
    elif corruption == 'backward_capture':
        source['seating_summary']['captured_at_ms'] = 1001. * 1000
    elif corruption == 'same_frame_changed_capture':
        source['frame_id'] = 14
    elif corruption == 'session_mismatch':
        source['seating_summary']['source_session_id'] = 'old-session'
    elif corruption == 'demo':
        source['is_demo'] = True
    elif corruption == 'image':
        source['kind'] = 'image'
    else:
        source['age_ms'] = 1000
    result = update(gate, source, 1006.)
    assert not result['can_depart'] and result['progress_ms'] == 0


def test_standing_confirmation_expires_without_fresh_frames():
    gate = DepartureInterlock()
    assert confirm(gate)['can_depart']
    result = gate.result(1006.6)
    assert not result['can_depart'] and result['progress_ms'] == 0


def test_fast_capture_clock_cannot_manufacture_five_seconds():
    gate = DepartureInterlock()
    for frame in range(20):
        now = 1000 + frame * .05
        source = packet(frame, now)
        source['seating_summary']['captured_at_ms'] = 1000000 + frame * 800
        result = update(gate, source, now)
    assert not result['can_depart'] and result['progress_ms'] < 1000


class Clock:
    now = 1000.

    def __call__(self):
        return self.now


def closed_stop():
    clock = Clock()
    controller = AssistanceController(clock=clock)
    controller.posture_enabled = True
    controller.scenario_control('start')
    clock.now += .8
    controller.tick()
    clock.now = (controller.scenario.boarding_until + controller.MOVE_SECONDS
                 + controller.departure.STANDING_CONFIRM_SECONDS - controller.scenario.NOTICE_SECONDS)
    controller.tick()
    clock.now = controller.scenario.boarding_until
    controller.tick()
    clock.now += .8
    assert controller.tick()['vehicle']['doors'] == 'closed'
    return controller, clock


def observed(source):
    return dict(instance_id='bridge', events=[], sources={'inside': source})


def test_controller_departure_after_five_seconds_clear_preserves_unknown_seated_total():
    controller, clock = closed_stop()
    for frame in range(15):
        clock.now += .4
        state = controller.tick(observed(packet(frame, clock.now)))
        assert state['seating']['seated'] is None
        if frame < 14:
            assert state['vehicle']['motion'] == 'stationary'
    assert state['vehicle']['motion'] == 'moving'
    assert state['readiness']['posture']['mode'] == 'standing_only'
    assert state['readiness']['posture']['message'].startswith('No standing detected.')


def test_no_standing_reminder_or_confirmation_until_doors_fully_closed():
    clock = Clock()
    controller = AssistanceController(clock=clock)
    controller.posture_enabled = True
    controller.scenario_control('start')
    for frame, elapsed in enumerate((.4, .4, 3.8, 6.2, .4)):
        clock.now += elapsed
        state = controller.tick(observed(packet(frame, clock.now, 'standing')))
        assert state['vehicle']['doors'] in {'opening', 'open', 'closing'}
        assert state['seating'] is None
        assert state['scenario']['posture_wait']['status'] == 'inactive'
        assert state['readiness']['posture']['progress_ms'] == 0
        assert 'seat' not in state['announcements'][0].lower()


def test_standing_holds_stationary_bus_indefinitely_then_only_reminds_while_moving():
    controller, clock = closed_stop()
    for frame in range(30):
        clock.now += .4
        state = controller.tick(observed(packet(frame, clock.now, 'standing')))
        assert state['vehicle']['motion'] == 'stationary'
    for frame in range(30, 44):
        clock.now += .4
        state = controller.tick(observed(packet(frame, clock.now)))
    assert state['vehicle']['motion'] == 'moving'
    clock.now += .4
    state = controller.tick(observed(packet(44, clock.now, 'standing')))
    assert state['vehicle']['motion'] == 'moving'
    assert state['announcements'][0] == controller.scenario.STANDING_REMINDER
    reminder_id = state['scenario']['announcement']['id']
    clock.now += .4
    repeat = controller.tick(observed(packet(45, clock.now, 'standing')))
    assert repeat['scenario']['announcement']['id'] == reminder_id
    assert repeat['vehicle']['motion'] == 'moving'


def test_old_door_epoch_cannot_supply_standing_only_clearance():
    controller, clock = closed_stop()
    old_epoch = controller.detection_policy()['posture_generation']
    controller.vehicle['doors'] = 'opening'
    controller.detection_policy()
    controller.vehicle['doors'] = 'closed'
    assert controller.detection_policy()['posture_generation'] != old_epoch
    for frame in range(15):
        clock.now += .4
        source = packet(frame, clock.now)
        source['seating_summary']['door_generation'] = old_epoch
        state = controller.tick(observed(source))
    assert state['vehicle']['motion'] == 'stationary'
    assert state['seating'] is None
    assert not state['readiness']['posture']['can_depart']


def test_next_stop_requires_new_closed_door_clearance_after_previous_trip_departed():
    controller, clock = closed_stop()
    old_generation = controller.detection_policy()['posture_generation']
    for frame in range(15):
        clock.now += .4
        old_clear = packet(frame, clock.now)
        old_clear['seating_summary']['door_generation'] = old_generation
        state = controller.tick(observed(old_clear))
    assert state['vehicle']['motion'] == 'moving'
    previous_cycle = state['scenario']['cycle_id']

    controller.scenario_control('stop')
    clock.now += controller.scenario.BRAKE_SECONDS
    assert controller.tick()['vehicle']['doors'] == 'opening'
    clock.now += controller.MOVE_SECONDS
    state = controller.tick()
    assert state['vehicle']['doors'] == 'open'
    assert state['scenario']['cycle_id'] == previous_cycle + 1
    clock.now = (controller.scenario.boarding_until + controller.MOVE_SECONDS
                 + controller.departure.STANDING_CONFIRM_SECONDS - controller.scenario.NOTICE_SECONDS)
    controller.tick()
    clock.now = controller.scenario.boarding_until
    controller.tick()
    clock.now += controller.MOVE_SECONDS
    assert controller.tick()['vehicle']['doors'] == 'closed'
    assert controller.detection_policy()['posture_generation'] != old_generation

    # A previously valid negative from the last stop is not authorization to move.
    for _ in range(12):
        clock.now += .4
        state = controller.tick(observed(copy.deepcopy(old_clear)))
        assert state['vehicle']['motion'] == 'stationary'
        assert not state['readiness']['posture']['can_depart']
        assert state['readiness']['posture']['progress_ms'] == 0
    clock.now += .4
    state = controller.tick(observed(packet(20, clock.now, 'standing')))
    assert state['readiness']['posture']['status'] == 'standing'
    assert state['vehicle']['motion'] == 'stationary'
    clear_began = clock.now + .4
    for frame in range(21, 35):
        clock.now += .4
        state = controller.tick(observed(packet(frame, clock.now)))
        if clock.now - clear_began < controller.departure.STANDING_CONFIRM_SECONDS:
            assert state['vehicle']['motion'] == 'stationary'
    assert state['vehicle']['motion'] == 'moving'
    assert state['readiness']['posture']['can_depart']
