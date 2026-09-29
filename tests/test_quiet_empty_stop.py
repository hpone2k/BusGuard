"""Empty-looking or disconnected feeds cannot bypass enabled posture checks."""

import pytest

from vision.assistance import AssistanceController


class Clock:
    def __init__(self):
        self.now = 1000.

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def system(tmp_path):
    clock = Clock()
    controller = AssistanceController(tmp_path / 'quiet-stop.json', clock)
    controller.posture_enabled = True
    return controller, clock


def open_stop(controller, clock):
    controller.scenario_control('start')
    clock.advance(controller.MOVE_SECONDS)
    state = controller.tick()
    assert state['vehicle']['doors'] == 'open'
    assert state['scenario']['boarding_remaining_ms'] == 15000
    return state


def finish_stop(controller, clock):
    notice = controller.snapshot()['scenario']['departure_notice']
    if not notice['announced'] and notice['remaining_ms'] is not None:
        clock.advance(max(0, (notice['remaining_ms'] - 10000) / 1000))
        controller.tick()
    clock.now = controller.scenario.boarding_until
    assert controller.tick()['vehicle']['doors'] == 'closing'
    clock.advance(controller.MOVE_SECONDS)
    assert controller.tick()['vehicle']['doors'] == 'closed'
    clock.advance(controller.scenario.DEPART_SECONDS)
    return controller.tick()


def cabin_packet(clock, posture='standing'):
    """One fresh posture is evidence even before a stable capacity vote exists."""
    now = clock() * 1000
    return dict(instance_id='quiet-stop-camera', events=[], sources={'inside': dict(
        kind='live', connected=True, is_demo=False, session_id='inside-one',
        frame_id=1, age_ms=0, received_at_ms=now, posture_enabled=True,
        prompt_mode='objects', count_summary=None,
        seating_summary=dict(status='observed', age_ms=0, captured_at_ms=now,
                             people=1, seated=int(posture == 'seated'),
                             standing=int(posture == 'standing'), unknown=int(posture == 'unknown'),
                             complete=posture != 'unknown',
                             occupants=[dict(track_id=1, posture=posture)]))})


def disconnect(controller):
    return controller.tick(dict(instance_id='quiet-stop-camera', events=[], sources={
        'inside': dict(kind='live', connected=False, is_demo=False,
                       posture_enabled=True, seating_summary=None)}))


def test_empty_disconnected_demo_closes_at_fifteen_and_holds_for_fresh_camera_evidence(system):
    controller, clock = system
    open_stop(controller, clock)
    clock.now = controller.scenario._planned_departure() - controller.scenario.NOTICE_SECONDS
    assert controller.tick()['scenario']['departure_notice']['announced']
    clock.now = controller.scenario.boarding_until - .01
    assert disconnect(controller)['vehicle']['doors'] == 'open'
    clock.now = controller.scenario.boarding_until
    assert controller.tick()['vehicle']['doors'] == 'closing'
    clock.advance(controller.MOVE_SECONDS)
    state = controller.tick()
    assert state['vehicle']['doors'] == 'closed'
    assert state['readiness']['posture']['status'] == 'unknown'
    clock.advance(2.99)
    assert controller.tick()['vehicle']['motion'] == 'stationary'
    clock.advance(.01)
    assert controller.tick()['vehicle']['motion'] == 'stationary'
    cycle = controller.scenario.cycle_id
    clock.advance(300)
    state = controller.tick()
    assert state['vehicle']['motion'] == 'stationary'
    assert state['scenario']['cycle_id'] == cycle
    assert state['scenario']['inside_detection_enabled']
    assert not state['readiness']['can_depart']


@pytest.mark.parametrize('known_source', ['confirmed', 'retained_camera'])
def test_known_passenger_requires_camera_even_without_activity_this_stop(system, known_source):
    controller, clock = system
    if known_source == 'confirmed':
        controller.scenario_control('load', priority_occupied=0, standard_occupied=1)
    else:
        controller.cabin.count = 1
    open_stop(controller, clock)
    state = finish_stop(controller, clock)
    assert state['scenario']['seats']['total'] == (1 if known_source == 'confirmed' else 0)
    assert state['vehicle']['motion'] == 'stationary'
    assert state['scenario']['phase'] == 'held'
    assert state['readiness']['posture']['status'] != 'not_required'


@pytest.mark.parametrize('posture', ['standing', 'unknown', 'seated'])
def test_person_seen_during_open_doors_cannot_disappear_into_empty_exemption(system, posture):
    controller, clock = system
    open_stop(controller, clock)
    clock.advance(1)
    controller.tick(cabin_packet(clock, posture))
    assert controller.cabin.count is None  # Not relying on a persisted capacity count.
    clock.advance(1)
    disconnect(controller)
    state = finish_stop(controller, clock)
    assert state['vehicle']['motion'] == 'stationary'
    assert state['scenario']['phase'] == 'held'
    assert state['readiness']['posture']['status'] != 'not_required'


def test_raw_person_before_pose_or_count_confirmation_prevents_empty_exemption(system):
    controller, clock = system
    open_stop(controller, clock)
    packet = cabin_packet(clock)
    source = packet['sources']['inside']
    source['seating_summary'] = None
    source['detections'] = [dict(label='person', score=.9, predicted=False, track_id=1)]
    controller.tick(packet)
    clock.advance(1)
    disconnect(controller)
    assert controller.cabin.count is None
    assert finish_stop(controller, clock)['vehicle']['motion'] == 'stationary'


def test_existing_closed_door_roster_survives_begin_reset(system):
    controller, clock = system
    controller.tick(cabin_packet(clock))
    assert controller.departure.result(clock())['expected_people'] == 1
    open_stop(controller, clock)
    disconnect(controller)
    assert finish_stop(controller, clock)['vehicle']['motion'] == 'stationary'


def test_seen_passenger_remains_known_across_restart_and_operator_reset(system):
    controller, clock = system
    open_stop(controller, clock)
    controller.tick(cabin_packet(clock))
    disconnect(controller)
    restored = AssistanceController(controller.path, clock)
    assert restored.snapshot()['vehicle']['revalidation_required']
    restored.scenario_control('reset')
    clock.advance(restored.MOVE_SECONDS)
    assert restored.tick()['vehicle']['doors'] == 'open'
    state = finish_stop(restored, clock)
    assert state['vehicle']['motion'] == 'stationary'
    assert state['readiness']['posture']['status'] != 'not_required'


def test_positive_count_without_pose_is_retained_as_evidence_after_count_falls_and_reset(system):
    controller, clock = system
    open_stop(controller, clock)
    controller.control('cabin_coverage', value=True)

    def count_frame(frame, people):
        packet = cabin_packet(clock)
        source = packet['sources']['inside']
        source.update(frame_id=frame, seating_summary=None, detections=[], count_summary={
            'age_ms': 0, 'coverage': 1,
            'classes': {'person': {'status': 'stable', 'stable': people, 'support': 1}}})
        return controller.tick(packet)

    count_frame(1, 1)
    clock.advance(.6)
    count_frame(2, 1)
    assert controller.cabin.count == 1
    # A whole-cabin decrease can update capacity, but cannot erase prior
    # passenger evidence merely because no pose was returned for that person.
    for frame in range(3, 9):
        clock.advance(.5)
        count_frame(frame, 0)
    assert controller.cabin.count == 0
    controller.scenario_control('reset')
    clock.advance(controller.MOVE_SECONDS)
    assert controller.tick()['vehicle']['doors'] == 'open'
    state = finish_stop(controller, clock)
    assert state['scenario']['seats']['total'] == 0
    assert state['vehicle']['motion'] == 'stationary'
    assert state['readiness']['posture']['status'] != 'not_required'


def test_expired_accepted_request_does_not_turn_active_stop_into_quiet_empty_stop(system):
    controller, clock = system
    open_stop(controller, clock)
    clock.advance(8)
    controller.create_request(dict(client_request_id='quiet-expiry', request_token='q' * 48,
                                   journey='boarding', stop_id='campus', needs=['extra_time'],
                                   location={'mode': 'at_stop', 'stop_id': 'campus'}))
    state = finish_stop(controller, clock)
    assert state['active_count'] == 0
    assert state['scenario']['seats']['total'] == 0
    assert state['scenario']['seats']['reserved'] == 0
    assert state['vehicle']['motion'] == 'stationary'
    assert state['readiness']['posture']['status'] != 'not_required'


def test_manual_mode_keeps_strict_camera_requirement(system):
    controller, _ = system
    state = controller.snapshot()
    assert not state['scenario']['enabled']
    assert not state['readiness']['can_depart']
    assert state['readiness']['posture']['status'] != 'not_required'


def test_empty_demo_does_not_bypass_unsecured_mobility(system):
    controller, clock = system
    open_stop(controller, clock)
    controller.vehicle.update(wheelchair_aboard=True, wheelchair_secured=False)
    state = finish_stop(controller, clock)
    assert state['vehicle']['motion'] == 'stationary'
    assert any('mobility area' in reason for reason in state['readiness']['reasons'])


def test_empty_demo_does_not_bypass_obstruction(system):
    controller, clock = system
    open_stop(controller, clock)
    controller.control('obstruction', value=True)
    clock.advance(60)
    state = controller.tick()
    assert state['vehicle']['doors'] == 'open'
    assert state['vehicle']['motion'] == 'stationary'
    assert any('obstruction' in reason for reason in state['readiness']['reasons'])
