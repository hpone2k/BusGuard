"""RFID/boarding records are immediate; only complete camera means reconcile seats."""
import json

from vision.assistance import AssistanceController
from test_cabin_count import source


class Clock:
    def __init__(self):
        self.now = 100.

    def __call__(self):
        return self.now


def system(*, scenario=False, priority=0, standard=0):
    clock = Clock()
    controller = AssistanceController(clock=clock)
    controller.control('cabin_coverage', value=True)
    controller.scenario_control('load', priority_occupied=priority, standard_occupied=standard)
    if scenario:
        open_stop(controller, clock)
    return controller, clock


def open_stop(controller, clock):
    controller.scenario_control('start')
    clock.now += .8
    controller.tick()


def frame(controller, clock, people, *, outside=False, label='person', **changes):
    inside = source(people, round(clock.now * 1000), clock.now, **changes)
    inside['count_summary']['last_observed_ms'] = clock.now * 1000
    if label != 'person':
        inside['count_summary']['classes'][label] = inside['count_summary']['classes'].pop('person')
    sources = {'inside': inside}
    if outside:
        sources['outside'] = dict(inside, session_id='outside-test', detections=[
            dict(label='person', score=.9, predicted=False)])
    return controller.tick({'instance_id': 'capacity-test', 'sources': sources, 'events': []})


def frames(controller, clock, people, duration=30., *, outside=False, label='person'):
    """Quarter-second captures, while the audit gives each second equal weight."""
    start = clock.now
    state = None
    for index in range(round(duration * 4) + 1):
        clock.now = start + index / 4
        count = people[min(index // 4, len(people) - 1)] if isinstance(people, list) else people
        state = frame(controller, clock, count, outside=outside, label=label)
    return state


def boarding_request(controller, key):
    return controller.create_request(dict(client_request_id=key, request_token='a' * 48,
                                          journey='boarding', stop_id='campus', needs=['extra_time'],
                                          location={'mode': 'at_stop', 'stop_id': 'campus'}))


def test_instant_camera_count_does_not_change_seats_until_all_thirty_seconds():
    controller, clock = system(priority=2, standard=3)
    state = frames(controller, clock, 7, duration=29.75)
    assert state['scenario']['seats']['total'] == 5
    assert state['scenario']['seats']['camera_check']['last_mean'] is None
    clock.now += .25
    seats = frame(controller, clock, 7)['scenario']['seats']
    assert seats['total'] == 7
    assert seats['confirmed_total'] == 5
    assert seats['standard']['available'] == 15
    assert seats['priority']['available'] == 4
    assert seats['camera_check']['last_mean'] == 7
    assert seats['camera_check']['mismatch'] == 2
    assert seats['estimated']


def test_each_one_second_count_has_equal_weight_and_mean_rounds_half_up():
    controller, clock = system()
    seats = frames(controller, clock, [7] * 20 + [6] * 5 + [5] * 5)['scenario']['seats']
    assert seats['camera_check']['last_mean'] == 6.5
    assert seats['camera_check']['last_rounded'] == 7
    assert seats['total'] == 7


def test_camera_mean_below_rfid_records_corrects_total_without_erasing_records():
    controller, clock = system(priority=2, standard=3)
    seats = frames(controller, clock, 3)['scenario']['seats']
    assert seats['total'] == 3
    assert seats['available'] == 23
    assert seats['confirmed_total'] == 5
    assert seats['camera_check']['mismatch'] == -2
    assert controller.scenario.camera_adjustment == -2


def test_taps_after_mean_apply_one_delta_and_duplicate_events_never_count_twice():
    controller, clock = system()
    frames(controller, clock, 25)
    open_stop(controller, clock)
    state = controller.scenario_rfid('tap1', 'person', simulated=True)
    assert state['scenario']['last_admission']['allowed']
    assert state['scenario']['seats']['total'] == 26
    assert controller.scenario_rfid('tap1', 'person', simulated=True)['scenario']['seats']['total'] == 26
    state = controller.scenario_rfid('tap2', 'person', simulated=True)
    assert not state['scenario']['last_admission']['allowed']
    state = controller.scenario_control('alight', seat_type='standard', event_id='exit1')
    assert state['scenario']['seats']['total'] == 25
    assert controller.scenario_control('alight', seat_type='standard', event_id='exit1')['scenario']['seats']['total'] == 25


def test_scan_after_downward_correction_still_changes_current_total_by_one():
    controller, clock = system(standard=5)
    frames(controller, clock, 3)
    open_stop(controller, clock)
    state = controller.scenario_rfid('next-board', 'person', simulated=True)
    assert state['scenario']['seats']['total'] == 4
    assert state['scenario']['seats']['confirmed_total'] == 6
    state = controller.scenario_control('alight', seat_type='standard', event_id='next-exit')
    assert state['scenario']['seats']['total'] == 3


def test_late_window_completion_preserves_scan_after_its_thirty_second_boundary():
    controller, clock = system(scenario=True)
    start = clock.now
    frames(controller, clock, 7, duration=29.75, outside=True)
    clock.now = start + 30.05
    controller.scenario_rfid('after-window', 'person', simulated=True)
    clock.now = start + 30.1
    seats = frame(controller, clock, 7, outside=True)['scenario']['seats']
    assert seats['camera_check']['last_mean'] == 7
    assert seats['confirmed_total'] == 1
    assert seats['total'] == 8


def test_raw_record_above_seat_group_capacity_after_correction_can_restart(tmp_path):
    controller, clock = system(priority=6, standard=20)
    controller.path = tmp_path / 'state.json'
    frames(controller, clock, 3)
    open_stop(controller, clock)
    state = controller.scenario_rfid('post-correction', 'person', simulated=True)
    assert state['scenario']['seats']['total'] == 4
    assert state['scenario']['seats']['confirmed_total'] == 27
    restored = AssistanceController(controller.path, clock)
    assert not getattr(restored, '_journal_error', False)
    assert restored.snapshot()['scenario']['seats']['total'] == 4


def test_camera_correction_respects_existing_reservations_without_negative_availability():
    controller, clock = system(scenario=True)
    frames(controller, clock, 26, duration=29, outside=True)
    boarding_request(controller, 'one')
    state = frames(controller, clock, 26, duration=1, outside=True)
    seats = state['scenario']['seats']
    assert seats['total'] == 26
    assert seats['standard']['reserved'] == 1
    assert seats['available'] == 0
    assert min(seats['standard']['available'], seats['priority']['available']) == 0
    assert not controller.scenario_rfid('full', 'senior')['scenario']['last_admission']['allowed']


def test_fast_overcapacity_safety_hold_does_not_wait_thirty_seconds():
    controller, clock = system()
    state = frames(controller, clock, 27, duration=.75)
    assert state['scenario']['seats']['total'] == 0
    assert any('more than 26' in reason for reason in state['readiness']['reasons'])


def test_completed_camera_correction_survives_restart_but_partial_samples_do_not(tmp_path):
    controller, clock = system()
    controller.path = tmp_path / 'state.json'
    frames(controller, clock, 26)
    restored = AssistanceController(controller.path, clock)
    seats = restored.snapshot()['scenario']['seats']
    assert seats['total'] == 26
    assert seats['available'] == 0
    assert seats['camera_check']['last_mean'] == 26
    assert seats['camera_check']['status'] == 'unavailable'
    assert seats['camera_check']['collected'] == 0
    assert restored.snapshot()['vehicle']['revalidation_required']


def test_legacy_retained_instant_count_is_not_restored_as_current_seat_occupancy(tmp_path):
    controller, clock = system()
    controller.path = tmp_path / 'state.json'
    frames(controller, clock, 4, duration=.75)
    journal = json.loads(controller.path.read_text())
    assert journal['cabin_count']['count'] == 4
    restored = AssistanceController(controller.path, clock)
    seats = restored.snapshot()['scenario']['seats']
    assert seats['total'] == 0
    assert seats['available'] == 26
    assert seats['camera_check']['last_mean'] is None


def test_app_confirmation_releases_reservation_without_adding_passenger():
    controller, clock = system(scenario=True)
    first = boarding_request(controller, 'first')
    second = boarding_request(controller, 'second')
    assert controller.snapshot()['scenario']['seats']['reserved'] == 2
    controller.scenario_rfid('actual-tap', 'person', simulated=True)
    controller.complete_request(first['id'], 'a' * 48)
    controller.complete_request(second['id'], 'a' * 48)
    controller.complete_request(second['id'], 'a' * 48)
    seats = controller.snapshot()['scenario']['seats']
    assert seats['total'] == seats['confirmed_total'] == 1
    assert seats['reserved'] == 0
    assert controller.scenario.movement_balance == 1


def test_app_alighting_completion_does_not_subtract_without_a_scan():
    controller, clock = system(scenario=True, standard=5)
    item = controller.create_request(dict(client_request_id='exit', request_token='a' * 48,
                                          journey='alighting', stop_id='campus', needs=['extra_time'],
                                          location={'mode': 'onboard'}))
    controller.complete_request(item['id'], 'a' * 48)
    assert controller.snapshot()['scenario']['seats']['total'] == 5
    assert controller.scenario.movement_balance == 0


def test_new_camera_baseline_never_extends_boarding_window():
    controller, clock = system(scenario=True)
    deadline = controller.scenario.boarding_until
    frames(controller, clock, 7, duration=2)
    assert controller.scenario.boarding_until == deadline
    assert controller.snapshot()['scenario']['seats']['total'] == 0


def test_missing_camera_observations_never_count_as_zero_or_apply_partial_mean():
    controller, clock = system(standard=4)
    frames(controller, clock, 0, duration=15)
    clock.now += 1
    controller.tick({'instance_id': 'capacity-test', 'sources': {}, 'events': []})
    state = frames(controller, clock, 0, duration=15)
    assert state['scenario']['seats']['total'] == 4
    assert state['scenario']['seats']['camera_check']['last_mean'] is None


def test_explicit_simulation_load_resets_camera_correction_and_sampling():
    controller, clock = system()
    frames(controller, clock, 9)
    state = controller.scenario_control('load', priority_occupied=0, standard_occupied=0)
    assert state['scenario']['seats']['total'] == 0
    assert state['scenario']['seats']['camera_check']['last_mean'] is None
    assert state['scenario']['seats']['camera_check']['collected'] == 0


def test_signed_movement_and_corrected_total_survive_restart_without_replaying_events(tmp_path):
    controller, clock = system()
    controller.path = tmp_path / 'state.json'
    frames(controller, clock, 5)
    open_stop(controller, clock)
    controller.scenario_control('alight', seat_type='standard', event_id='exit')
    assert controller.scenario.movement_balance == -1
    restored = AssistanceController(controller.path, clock)
    assert restored.scenario.movement_balance == -1
    assert restored.snapshot()['scenario']['seats']['total'] == 4
