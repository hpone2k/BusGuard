"""Timed journeys cannot bypass cabin standing checks by disabling the option."""
import pytest

from vision.assistance import AssistanceController
from vision.schema import Detection
from vision.standing import estimate


class Clock:
    now = 1000.

    def __call__(self):
        return self.now


def closed_stop():
    clock = Clock()
    controller = AssistanceController(clock=clock)
    controller.scenario_control('start')
    clock.now += controller.MOVE_SECONDS
    controller.tick()
    clock.now = (controller.scenario.boarding_until + controller.MOVE_SECONDS
                 + controller.scenario.DEPART_SECONDS - controller.scenario.NOTICE_SECONDS)
    controller.tick()
    clock.now = controller.scenario.boarding_until
    controller.tick()
    clock.now += controller.MOVE_SECONDS
    assert controller.tick()['vehicle']['doors'] == 'closed'
    return controller, clock


def observation(frame, now, *, enabled=True, standing=False):
    person = Detection('person', [.1, .1, .4, .9], .9, track_id=1)
    positive = [Detection('standing person', person.bbox, .9)] if standing else []
    summary = estimate([person], [person], positive, now * 1000)
    return dict(instance_id='required-standing-camera', events=[], sources={'inside': dict(
        kind='live', connected=True, is_demo=False, session_id='inside', frame_id=frame,
        received_at_ms=now * 1000, age_ms=0, posture_enabled=enabled, prompt_mode='objects',
        seating_summary=summary,
        count_summary=dict(age_ms=0, coverage=1,
                           classes={'person': dict(stable=1, status='stable', support=1)}))})


@pytest.mark.parametrize('source', ['missing', 'disabled_clear_frames'])
def test_timed_departure_cannot_bypass_disabled_standing_check(source):
    controller, clock = closed_stop()
    for frame in range(30):
        clock.now += .4
        packet = observation(frame, clock.now, enabled=False) if source == 'disabled_clear_frames' else None
        state = controller.tick(packet)
        assert state['vehicle']['motion'] == 'stationary'
        assert not state['readiness']['can_depart']
        assert 'Enable the inside camera standing check before departure.' in state['readiness']['reasons']
    assert state['scenario']['phase'] == 'held'
    assert state['readiness']['posture_enabled'] is False
    assert state['readiness']['posture'] is None  # The controller never invents camera evidence.


def test_enabled_but_unavailable_cabin_still_holds_departure():
    controller, clock = closed_stop()
    controller.posture_enabled = True
    clock.now += 20
    state = controller.tick()
    assert state['vehicle']['motion'] == 'stationary'
    assert not state['readiness']['posture']['can_depart']
    assert 'Fresh live cabin posture' in state['readiness']['posture']['message']


def test_required_check_holds_standing_then_departs_after_fresh_five_second_clearance():
    controller, clock = closed_stop()
    for frame in range(30):
        clock.now += .4
        state = controller.tick(observation(frame, clock.now, standing=True))
        assert state['vehicle']['motion'] == 'stationary'
        assert not state['readiness']['can_depart']
    for frame in range(30, 44):
        clock.now += .4
        state = controller.tick(observation(frame, clock.now))
        if frame < 43:
            assert state['vehicle']['motion'] == 'stationary'
    assert state['vehicle']['motion'] == 'moving'
    assert state['readiness']['posture']['can_depart']


def test_manual_mode_keeps_optional_standing_preference():
    controller = AssistanceController(clock=Clock())
    assert not controller.scenario.enabled and not controller.posture_enabled
    assert controller.snapshot()['readiness']['can_depart']
    assert controller.control('depart')['vehicle']['motion'] == 'moving'


def test_previous_stop_clearance_cannot_depart_again_without_new_cabin_evidence():
    controller, clock = closed_stop()
    for frame in range(15):
        clock.now += .4
        state = controller.tick(observation(frame, clock.now))
    assert state['vehicle']['motion'] == 'moving'
    previous_cycle = state['scenario']['cycle_id']
    controller.scenario_control('stop')
    clock.now += controller.scenario.BRAKE_SECONDS
    controller.tick()
    clock.now += controller.MOVE_SECONDS
    controller.tick()
    clock.now = (controller.scenario.boarding_until + controller.MOVE_SECONDS
                 + controller.scenario.DEPART_SECONDS - controller.scenario.NOTICE_SECONDS)
    controller.tick()
    clock.now = controller.scenario.boarding_until
    controller.tick()
    clock.now += controller.MOVE_SECONDS
    controller.tick()
    clock.now += 30
    state = controller.tick()
    assert state['scenario']['cycle_id'] != previous_cycle
    assert state['vehicle']['doors'] == 'closed'
    assert state['vehicle']['motion'] == 'stationary'
    assert state['readiness']['posture_enabled']
    assert not state['readiness']['posture']['can_depart']
