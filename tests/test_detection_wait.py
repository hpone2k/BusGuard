"""Outside observations renew boarding; actual departure always has notice."""

import copy

import pytest

from vision.assistance import AssistanceController
from timing_helpers import assume_clear_standing_check, use_real_standing_check


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
    controller = AssistanceController(tmp_path / 'detection-wait.json', clock)
    assume_clear_standing_check(controller)
    return controller, clock


def open_stop(controller, clock):
    controller.scenario_control('start')
    clock.advance(controller.MOVE_SECONDS)
    state = controller.tick()
    assert state['vehicle']['doors'] == 'open'
    return state


def packet(clock, frame=1, *, role='outside', detections=None, **source_fields):
    source = dict(kind='live', connected=True, is_demo=False,
                  session_id='boarding-camera', frame_id=frame,
                  received_at_ms=clock() * 1000, age_ms=0,
                  detections=[dict(label='person', score=.9, predicted=False, track_id=1)]
                  if detections is None else detections)
    source.update(source_fields)
    return dict(instance_id='boarding-bridge', sources={role: source}, events=[])


def warn_on_schedule(controller, clock):
    notice = controller.snapshot()['scenario']['departure_notice']
    if not notice['announced'] and notice['remaining_ms'] > 10000:
        clock.advance((notice['remaining_ms'] - 10000) / 1000)
    return controller.tick()


def close_and_wait(controller, clock):
    clock.now = controller.scenario.boarding_until
    assert controller.tick()['vehicle']['doors'] == 'closing'
    clock.advance(controller.MOVE_SECONDS)
    assert controller.tick()['vehicle']['doors'] == 'closed'
    clock.advance(controller.scenario.DEPART_SECONDS)
    return controller.tick()


def test_quiet_stop_warns_ten_seconds_before_motion_not_before_door_closure(system):
    c, clock = system
    state = open_stop(c, clock)
    notice = state['scenario']['departure_notice']
    assert notice['remaining_ms'] == 18800
    assert not notice['announced']
    clock.advance(8.7)
    assert not c.tick()['scenario']['departure_notice']['announced']
    clock.advance(.1)
    state = c.tick()
    assert state['scenario']['departure_notice']['announced']
    assert state['scenario']['departure_notice']['remaining_ms'] == 10000
    assert state['scenario']['departure_notice']['minimum_remaining_ms'] == 10000
    warning_id = state['scenario']['announcement']['id']
    assert '10' in state['scenario']['announcement']['message']
    clock.advance(1)
    assert c.tick()['scenario']['announcement']['id'] == warning_id
    assert close_and_wait(c, clock)['vehicle']['motion'] == 'moving'
    clock.advance(100)
    assert c.tick()['vehicle']['motion'] == 'moving'


def test_new_outside_frames_wait_fifteen_seconds_from_receipt_and_clear_visual_active(system):
    c, clock = system
    open_stop(c, clock)
    clock.advance(8)
    state = c.tick(packet(clock))
    assert state['scenario']['boarding_remaining_ms'] == 15000
    assert state['scenario']['detection_wait']['active']
    assert state['scenario']['detection_wait']['roles'] == ['outside']
    assert state['scenario']['departure_notice']['waiting_for_detection']
    clock.advance(2)
    assert c.tick()['vehicle']['doors'] == 'open'
    assert not c.snapshot()['scenario']['detection_wait']['active']
    assert c.snapshot()['scenario']['boarding_remaining_ms'] == 13000
    state = c.tick(packet(clock, 2))
    assert state['scenario']['boarding_remaining_ms'] == 15000
    clock.advance(.2)
    state = c.tick(packet(clock, 3, detections=[]))
    assert not state['scenario']['detection_wait']['active']
    assert state['scenario']['boarding_remaining_ms'] == 14800


def test_receipt_time_not_poll_time_determines_detection_extension(system):
    c, clock = system
    open_stop(c, clock)
    clock.advance(8)
    source = packet(clock)
    clock.advance(.4)
    source['sources']['outside']['age_ms'] = 400
    state = c.tick(source)
    assert state['scenario']['boarding_remaining_ms'] == 14600
    assert state['scenario']['detection_wait']['last_detection_age_ms'] == 400
    deadline = c.scenario.boarding_until
    clock.advance(.4)
    source['sources']['outside']['age_ms'] = 0  # A replay cannot renew its receipt.
    assert c.tick(source)['scenario']['boarding_remaining_ms'] == 14200
    assert c.scenario.boarding_until == deadline


def test_in_flight_pre_deadline_frame_can_extend_before_closure_has_started(system):
    c, clock = system
    open_stop(c, clock)
    clock.advance(14.9)
    pending = packet(clock)
    clock.advance(.2)
    pending['sources']['outside']['age_ms'] = 200
    state = c.tick(pending)
    assert state['vehicle']['doors'] == 'open'
    assert state['scenario']['boarding_remaining_ms'] == 14800
    assert c.scenario.boarding_until == pytest.approx(c.scenario.started_at + 29.9)


def test_in_flight_frame_cannot_reopen_a_closing_door_or_fifty_second_cap(system):
    c, clock = system
    open_stop(c, clock)
    clock.advance(14.9)
    pending = packet(clock)
    clock.advance(.1)
    assert c.tick()['vehicle']['doors'] == 'closing'
    clock.advance(.1)
    pending['sources']['outside']['age_ms'] = 200
    assert c.tick(pending)['vehicle']['doors'] == 'closing'
    assert c.scenario.boarding_until == pytest.approx(c.scenario.started_at + 15)


def test_frame_first_captured_after_quiet_deadline_cannot_revive_expired_boarding(system):
    c, clock = system
    open_stop(c, clock)
    clock.advance(15.1)
    state = c.tick(packet(clock))
    assert state['vehicle']['doors'] == 'closing'
    assert c.scenario.boarding_until == pytest.approx(c.scenario.started_at + 15)


def test_continuous_outside_detections_reach_fifty_second_cap_but_cannot_reopen_it(system):
    c, clock = system
    open_stop(c, clock)
    for frame, elapsed in enumerate(range(1, 50), 1):
        clock.now = c.scenario.started_at + elapsed
        state = c.tick(packet(clock, frame))
        assert c.scenario.boarding_until == pytest.approx(
            min(clock() + 15, c.scenario.started_at + 50))
        assert state['vehicle']['doors'] == 'open'
    clock.now = c.scenario.started_at + 50
    state = c.tick(packet(clock, 50))
    assert state['vehicle']['doors'] == 'closing'
    assert not state['scenario']['admission_open']
    deadline = c.scenario.boarding_until
    clock.advance(.2)
    c.tick(packet(clock, 51))
    assert c.scenario.boarding_until == deadline
    assert c.vehicle['doors'] == 'closing'


@pytest.mark.parametrize('change', [
    dict(kind='image'), dict(kind='video'), dict(connected=False), dict(is_demo=True),
    dict(age_ms=1000), dict(session_id=None), dict(frame_id=None),
    dict(received_at_ms=None), dict(received_at_ms=float('nan')),
    dict(detections=[]), dict(detections=[dict(label='person', score=.9, predicted=True)]),
])
def test_ineligible_observations_do_not_extend_window(system, change):
    c, clock = system
    open_stop(c, clock)
    original = c.scenario.boarding_until
    clock.advance(8)
    state = c.tick(packet(clock, **change))
    assert c.scenario.boarding_until == original
    assert not state['scenario']['detection_wait']['active']


@pytest.mark.parametrize('offset', [-9, 1])
def test_pre_stop_or_future_receipt_cannot_extend_even_if_reported_fresh(system, offset):
    c, clock = system
    open_stop(c, clock)
    original = c.scenario.boarding_until
    clock.advance(8)
    c.tick(packet(clock, received_at_ms=(clock() + offset) * 1000))
    assert c.scenario.boarding_until == original


def test_inside_people_never_extend_outside_boarding_wait(system):
    c, clock = system
    open_stop(c, clock)
    original = c.scenario.boarding_until
    for frame in range(1, 5):
        clock.advance(2)
        state = c.tick(packet(clock, frame, role='inside'))
        assert c.scenario.boarding_until == original
        assert not state['scenario']['detection_wait']['active']


def test_short_inside_counts_neither_replace_records_nor_extend_boarding(system):
    c, clock = system
    open_stop(c, clock)
    original = c.scenario.boarding_until
    clock.advance(8)
    for people in (1, 2):
        frame = people * 2 - 1
        observation = packet(clock, frame, role='inside', prompt_mode='objects', count_summary={
            'age_ms': 0, 'coverage': 1,
            'classes': {'person': {'status': 'stable', 'stable': people, 'support': 1}}})
        c.tick(observation)
        clock.advance(.6)
        observation['sources']['inside'].update(frame_id=frame + 1,
                                               received_at_ms=clock() * 1000)
        state = c.tick(observation)
        assert state['scenario']['seats']['total'] == 0
        assert state['scenario']['seats']['camera_check']['last_mean'] is None
        assert c.scenario.boarding_until == original


def test_duplicate_and_out_of_order_frames_do_not_extend_or_hide_newer_detection(system):
    c, clock = system
    open_stop(c, clock)
    clock.advance(7)
    c.tick(packet(clock, 5))
    deadline = c.scenario.boarding_until
    clock.advance(.2)
    state = c.tick(packet(clock, 4, detections=[]))
    assert state['scenario']['detection_wait']['active']
    clock.advance(.2)
    c.tick(packet(clock, 5))
    assert c.scenario.boarding_until == deadline
    c.tick(packet(clock, 6))
    assert c.scenario.boarding_until > deadline


def test_frozen_packet_cannot_extend_next_stop_or_travelling_bus(system):
    c, clock = system
    open_stop(c, clock)
    clock.advance(2)
    old = packet(clock)
    c.tick(old)
    warn_on_schedule(c, clock)
    assert close_and_wait(c, clock)['vehicle']['motion'] == 'moving'
    deadline = c.scenario.boarding_until
    c.tick(packet(clock, 2))
    assert c.vehicle['motion'] == 'moving'
    assert c.scenario.boarding_until == deadline
    c.scenario_control('stop')
    clock.advance(c.scenario.BRAKE_SECONDS)
    c.tick()
    clock.advance(c.MOVE_SECONDS)
    c.tick()
    next_deadline = c.scenario.boarding_until
    clock.advance(8)
    c.tick(copy.deepcopy(old))
    assert c.scenario.boarding_until == next_deadline


def test_arbitrary_outside_object_is_not_evidence_of_an_aboard_passenger(system):
    c, clock = system
    use_real_standing_check(c)
    open_stop(c, clock)
    clock.advance(8)
    c.tick(packet(clock, detections=[dict(label='laptop', score=.9, predicted=False)]))
    assert not c.scenario.passenger_evidence
    assert c.snapshot()['scenario']['seats']['total'] == 0
    warn_on_schedule(c, clock)
    state = close_and_wait(c, clock)
    assert state['readiness']['posture']['status'] == 'unknown'
    assert state['vehicle']['motion'] == 'stationary'


def test_late_controller_tick_still_delivers_a_full_ten_second_notice(system):
    c, clock = system
    open_stop(c, clock)
    clock.advance(15)
    state = c.tick()
    assert state['vehicle']['doors'] == 'closing'
    assert state['scenario']['departure_notice']['announced']
    warning_id = state['scenario']['announcement']['id']
    announced_at = clock()
    clock.advance(c.MOVE_SECONDS)
    state = c.tick()
    assert state['vehicle']['doors'] == 'closed'
    assert state['scenario']['announcement']['id'] == warning_id
    clock.advance(c.scenario.DEPART_SECONDS)
    state = c.tick()
    assert state['vehicle']['motion'] == 'stationary'
    assert state['scenario']['announcement']['id'] == warning_id
    clock.now = announced_at + 9.999
    assert c.tick()['vehicle']['motion'] == 'stationary'
    clock.now = announced_at + 10
    assert c.tick()['vehicle']['motion'] == 'moving'


def test_safety_announcement_overrides_departure_warning(system):
    c, clock = system
    open_stop(c, clock)
    assert warn_on_schedule(c, clock)['scenario']['departure_notice']['announced']
    state = c.control('obstruction', value=True)
    assert not state['scenario']['departure_notice']['announced']
    assert state['scenario']['departure_notice']['remaining_ms'] is None
    assert 'doorway clear' in state['announcements'][0]


def test_new_activity_reschedules_warning_then_announces_for_new_departure(system):
    c, clock = system
    open_stop(c, clock)
    first = warn_on_schedule(c, clock)
    assert first['scenario']['departure_notice']['announced']
    clock.advance(1)
    state = c.tick(packet(clock))
    assert not state['scenario']['departure_notice']['announced']
    assert state['scenario']['departure_notice']['remaining_ms'] == 18800
    warning = warn_on_schedule(c, clock)
    assert warning['scenario']['departure_notice']['announced']
    assert warning['scenario']['departure_notice']['minimum_remaining_ms'] == 10000
    assert warning['scenario']['announcement']['id'] != first['scenario']['announcement']['id']


def test_safety_hold_clearing_requires_a_fresh_ten_second_warning(system):
    c, clock = system
    open_stop(c, clock)
    warn_on_schedule(c, clock)
    c.vehicle.update(wheelchair_aboard=True, wheelchair_secured=False)
    assert close_and_wait(c, clock)['vehicle']['motion'] == 'stationary'
    clock.advance(30)
    state = c.control('secure_wheelchair', value=True)
    assert state['vehicle']['motion'] == 'stationary'
    assert state['scenario']['departure_notice']['minimum_remaining_ms'] == 10000
    assert state['scenario']['departure_notice']['announced']
    clock.advance(9.999)
    assert c.tick()['vehicle']['motion'] == 'stationary'
    clock.advance(.001)
    assert c.tick()['vehicle']['motion'] == 'moving'
