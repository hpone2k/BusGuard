"""Exercise the real simulated stop sequence with untracked cabin people."""
import pytest

from vision.assistance import AssistanceController
from vision.schema import Detection
from vision.standing import estimate


class Clock:
    now = 1000.

    def __call__(self):
        return self.now


def setup():
    clock = Clock()
    controller = AssistanceController(clock=clock)
    controller.posture_enabled = True
    controller.scenario_control('start')
    # Occupancy has its own RFID/camera reconciliation. It must not turn old
    # person identities into a permanent standing hold for this demonstration.
    controller.scenario.occupied['standard'] = 18
    return controller, clock


def step(controller, clock, frame, *, standing=False, connected=True):
    clock.now = round(clock.now + .2, 6)
    raw = [Detection('person', [x, .2, x + .2, .9], .9) for x in (.05, .35, .65)]
    positive = [Detection('standing person', raw[0].bbox, .8)] if standing else []
    summary = estimate([], raw, positive, clock.now * 1000)
    summary['door_generation'] = controller.detection_policy()['posture_generation']
    source = dict(kind='live', connected=connected, is_demo=False, session_id='inside-camera',
                  frame_id=frame, received_at_ms=clock.now * 1000, age_ms=0,
                  posture_enabled=True, prompt_mode='objects', detections=[], seating_summary=summary,
                  count_summary={'classes': {'person': {'status': 'warming', 'stable': None}}})
    return controller.tick(dict(instance_id='camera', events=[], sources={'inside': source}))


def test_no_standing_departs_after_five_seconds_despite_untracked_people_and_old_count():
    controller, clock = setup()
    closed_at = None
    for frame in range(140):
        state = step(controller, clock, frame)
        if state['vehicle']['doors'] != 'closed':
            assert state['readiness']['posture']['progress_ms'] == 0
            continue
        if closed_at is None:
            closed_at = clock.now
        if state['vehicle']['motion'] == 'moving':
            break
        assert clock.now - closed_at < 6
    assert state['vehicle']['motion'] == 'moving'
    assert 5 <= clock.now - closed_at <= 5.8
    assert state['readiness']['posture']['progress_ms'] == 5000
    assert state['scenario']['posture_wait']['confirmation_ms'] == 5000
    assert state['seating']['unknown'] == 3  # No sitting/identity claim was invented.
    assert state['seating']['standing'] == 0
    assert controller.scenario.occupied['standard'] == 18
    assert clock.now >= controller.scenario.notice_at + controller.scenario.NOTICE_SECONDS


@pytest.mark.parametrize('interruption', ['standing', 'disconnected'])
def test_interruption_restarts_the_full_five_second_check(interruption):
    controller, clock = setup()
    frame = 0
    while controller.departure.result(clock.now)['progress_ms'] < 3800:
        step(controller, clock, frame)
        frame += 1
        assert frame < 140
    interrupted = step(controller, clock, frame, standing=interruption == 'standing',
                       connected=interruption != 'disconnected')
    assert interrupted['readiness']['posture']['progress_ms'] == 0
    assert interrupted['vehicle']['motion'] == 'stationary'
    first_clear = clock.now + .2
    for _ in range(28):
        frame += 1
        state = step(controller, clock, frame)
        if clock.now - first_clear < 5 - 1e-6:
            assert state['vehicle']['motion'] == 'stationary'
        if state['vehicle']['motion'] == 'moving':
            break
    assert state['vehicle']['motion'] == 'moving'
    assert 5 - 1e-6 <= clock.now - first_clear <= 5.4


def test_valid_negative_standing_checks_do_not_override_emergency_hold():
    controller, clock = setup()
    for frame in range(140):
        if controller.vehicle['doors'] == 'closed':
            controller.vehicle['emergency'] = True
        state = step(controller, clock, frame)
    assert state['readiness']['posture']['can_depart']
    assert state['vehicle']['motion'] == 'stationary'
    assert any('Emergency hold' in reason for reason in state['readiness']['reasons'])
