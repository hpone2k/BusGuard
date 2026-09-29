"""End-to-end controller posture rules with deterministic camera time."""
import pytest

from vision.assistance import AssistanceController
from vision.seating import seating_evidence


class Clock:
    now = 1000.

    def __call__(self):
        return self.now


def packet(clock, frame, postures=('seated',), **changes):
    inside = dict(kind='live', connected=True, is_demo=False, session_id='inside',
                  frame_id=frame, age_ms=0, received_at_ms=clock.now * 1000,
                  posture_enabled=True, prompt_mode='objects',
                  count_summary={'age_ms': 0, 'coverage': 1,
                                 'classes': {'person': {'stable': len(postures), 'status': 'stable', 'support': 1}}},
                  seating_summary=dict(status='observed', age_ms=0, complete=True,
                                       captured_at_ms=clock.now * 1000, people=len(postures),
                                       seated=postures.count('seated'), standing=postures.count('standing'),
                                       unknown=postures.count('unknown'),
                                       occupants=[dict(track_id=index, posture=posture)
                                                  for index, posture in enumerate(postures)]))
    inside.update(changes)
    return dict(instance_id='bridge', events=[], sources={'inside': inside})


def closed_stop():
    clock = Clock()
    controller = AssistanceController(clock=clock)
    controller.posture_enabled = True
    controller.scenario_control('start')
    clock.now += .8
    controller.tick()
    planned_check = controller.departure.confirmation_seconds
    clock.now = (controller.scenario.boarding_until + controller.MOVE_SECONDS
                 + planned_check - controller.scenario.NOTICE_SECONDS)
    controller.tick()  # Timely ten-second departure notice, with no seating instruction.
    clock.now = controller.scenario.boarding_until
    controller.tick()
    clock.now += .8
    assert controller.tick()['vehicle']['doors'] == 'closed'
    # The initial plan allows five seconds before the camera identifies its
    # mode. Start these legacy seated checks once the existing notice can end
    # within their three-second confirmation, without shortening the notice.
    clock.now += max(0, planned_check - controller.departure.CONFIRM_SECONDS)
    controller.tick()
    return controller, clock


@pytest.mark.parametrize('posture', ['standing', 'unknown'])
def test_fresh_unseated_evidence_keeps_bus_stopped_beyond_old_timeout(posture):
    controller, clock = closed_stop()
    for frame in range(151):
        clock.now += .4
        state = controller.tick(packet(clock, frame, (posture,)))
        assert state['vehicle']['motion'] == 'stationary'
    assert state['scenario']['posture_wait']['remaining_ms'] is None
    assert state['scenario']['posture_wait']['bypassed'] is False


def test_everyone_seated_for_three_continuous_seconds_then_departure():
    controller, clock = closed_stop()
    # Closing, confirming for 2.4s, then standing must restart confirmation.
    for frame in range(8):
        clock.now += .4
        state = controller.tick(packet(clock, frame))
        assert state['vehicle']['motion'] == 'stationary'
    assert state['scenario']['posture_wait']['remaining_ms'] == 600
    clock.now += .4
    state = controller.tick(packet(clock, 8, ('standing',)))
    assert state['scenario']['posture_wait']['remaining_ms'] is None
    for frame in range(9, 17):
        clock.now += .4
        state = controller.tick(packet(clock, frame))
        assert state['vehicle']['motion'] == 'stationary'
    clock.now += .4
    assert controller.tick(packet(clock, 17))['vehicle']['motion'] == 'moving'


def test_stale_last_seated_frame_cannot_complete_confirmation():
    controller, clock = closed_stop()
    for frame in range(8):
        clock.now += .4
        last = packet(clock, frame)
        controller.tick(last)
    clock.now += 12
    state = controller.tick(last)
    assert state['vehicle']['motion'] == 'stationary'
    assert state['readiness']['posture']['progress_ms'] == 0


def test_no_posture_or_sit_reminder_during_opening_boarding_or_closing():
    clock = Clock()
    controller = AssistanceController(clock=clock)
    controller.posture_enabled = True
    controller.scenario_control('start')
    for frame, seconds in enumerate((.4, .4, 3.8, 6.2, .4)):
        clock.now += seconds
        state = controller.tick(packet(clock, frame, ('standing',)))
        assert state['vehicle']['doors'] in {'opening', 'open', 'closing'}
        assert state['seating'] is None
        assert state['scenario']['posture_wait']['status'] == 'inactive'
        assert 'seat' not in state['announcements'][0].lower()
        assert 'standing' not in state['announcements'][0].lower()


def test_moving_standing_announces_through_shared_channel_without_stopping_or_spam():
    controller, clock = closed_stop()
    for frame in range(10):
        clock.now += .4
        state = controller.tick(packet(clock, frame))
    assert state['vehicle']['motion'] == 'moving'
    clock.now += .4
    first = controller.tick(packet(clock, 10, ('standing',)))
    assert first['vehicle']['motion'] == 'moving'
    assert first['announcements'][0] == controller.scenario.STANDING_REMINDER
    assert first['scenario']['posture_wait']['status'] == 'monitoring'
    identifier = first['scenario']['announcement']['id']
    for frame in range(11, 21):
        clock.now += .4
        state = controller.tick(packet(clock, frame, ('standing',)))
        assert state['vehicle']['motion'] == 'moving'
        assert state['scenario']['announcement']['id'] == identifier
    clock.now += 21
    stale = controller.tick(packet(clock, 21, ('standing',), age_ms=2000))
    assert stale['scenario']['announcement']['id'] == identifier
    clock.now += .4
    repeated = controller.tick(packet(clock, 22, ('standing',)))
    assert repeated['vehicle']['motion'] == 'moving'
    assert repeated['scenario']['announcement']['id'] != identifier


@pytest.mark.parametrize('coverage,known,expected_motion', [(False, 0, 'stationary'),
                                                         (True, 1, 'stationary'),
                                                         (True, 0, 'moving')])
def test_empty_camera_requires_coverage_and_zero_known_occupancy(coverage, known, expected_motion):
    controller, clock = closed_stop()
    controller.cabin.configure(coverage)
    controller.scenario.occupied['standard'] = known
    for frame in range(10):
        clock.now += .4
        observed = packet(clock, frame, ())
        # Use the actual pose result from a blank camera frame. No person pose
        # exists, so complete=False must be handled as verified emptiness rather
        # than fabricated all-seated evidence.
        summary = seating_evidence([], [], [], clock.now * 1000)
        assert summary['status'] == 'observed' and summary['complete'] is False
        observed['sources']['inside']['seating_summary'] = summary
        state = controller.tick(observed)
    assert state['vehicle']['motion'] == expected_motion


@pytest.mark.parametrize('invalid', ['capacity', 'unavailable', 'inconsistent', 'unsteady_count', 'known_camera_person'])
def test_real_empty_pose_does_not_bypass_invalid_or_incomplete_population_evidence(invalid):
    controller, clock = closed_stop()
    controller.cabin.configure(True)
    if invalid == 'known_camera_person':
        controller.cabin.count = 1
    for frame in range(12):
        clock.now += .4
        observed = packet(clock, frame, ())
        inside = observed['sources']['inside']
        summary = seating_evidence([], [], [], clock.now * 1000, available=invalid != 'unavailable')
        if invalid == 'capacity':
            summary['capacity_reached'] = True
        elif invalid == 'inconsistent':
            summary['people'] = 1
        elif invalid == 'unsteady_count':
            inside['count_summary']['classes']['person']['status'] = 'warming'
        inside['seating_summary'] = summary
        state = controller.tick(observed)
    assert state['vehicle']['motion'] == 'stationary'
    assert state['readiness']['posture']['can_depart'] is False


def test_inside_detection_survives_motion_but_posture_rejects_old_door_generation():
    controller, clock = closed_stop()
    policy = controller.detection_policy()
    controller.vehicle['doors'] = 'opening'
    opening = controller.detection_policy()
    assert opening['inside_generation'] == policy['inside_generation']
    assert not opening['posture_enabled']
    controller.vehicle['doors'] = 'closed'
    after = controller.detection_policy()
    assert after['posture_generation'] != policy['posture_generation']
    for frame in range(10):
        clock.now += .4
        source = packet(clock, frame)
        source['sources']['inside']['seating_summary']['door_generation'] = policy['posture_generation']
        state = controller.tick(source)
    assert state['vehicle']['motion'] == 'stationary'
    assert state['seating'] is None


def test_missing_camera_never_uses_old_empty_demo_bypass():
    controller, clock = closed_stop()
    controller.cabin.configure(True)
    clock.now += 100
    state = controller.tick()
    assert state['vehicle']['motion'] == 'stationary'
    assert not state['readiness']['posture']['can_depart']
