"""Thirty-second cabin checks continue during travel independently of posture."""
from test_cabin_capacity import frames, system


def moving_controller():
    controller, clock = system(priority=2, standard=3)
    controller.control('depart')
    assert controller.vehicle['motion'] == 'moving'
    return controller, clock


def test_complete_mean_updates_travelling_seats_without_double_counting_records():
    controller, clock = moving_controller()
    state = frames(controller, clock, 7, label='persons')
    seats = state['scenario']['seats']
    assert seats['total'] == 7
    assert seats['confirmed_total'] == 5
    assert seats['available'] == 19
    assert seats['priority']['available'] == 4
    assert seats['standard']['available'] == 15
    assert seats['camera_check']['last_mean'] == 7
    assert not seats['count_source']['seat_locations_verified']
    assert state['vehicle']['motion'] == 'moving'


def test_camera_dropout_retains_last_completed_mean_without_stopping_moving_bus():
    controller, clock = moving_controller()
    frames(controller, clock, 22)
    frames(controller, clock, 5, duration=12)
    clock.now += 5
    state = controller.tick({'instance_id': 'capacity-test', 'sources': {}, 'events': []})
    assert state['scenario']['seats']['total'] == 22
    assert state['scenario']['seats']['available'] == 4
    assert state['scenario']['seats']['camera_check']['status'] == 'unavailable'
    assert state['scenario']['seats']['camera_check']['last_mean'] == 22
    assert state['vehicle']['motion'] == 'moving'


def test_new_complete_mean_may_correct_travelling_total_below_recorded_count():
    controller, clock = moving_controller()
    state = frames(controller, clock, 2)
    assert state['scenario']['seats']['total'] == 2
    assert state['scenario']['seats']['confirmed_total'] == 5
    assert state['scenario']['seats']['available'] == 24
    assert state['vehicle']['motion'] == 'moving'


def test_window_keeps_running_across_motion_transition():
    controller, clock = moving_controller()
    frames(controller, clock, 7, duration=15)
    controller.control('arrive', stop_id='community')
    state = frames(controller, clock, 7, duration=15)
    assert state['scenario']['seats']['camera_check']['last_mean'] == 7
    assert state['scenario']['seats']['total'] == 7
    assert state['vehicle']['motion'] == 'stationary'


def test_each_completed_cycle_replaces_prior_correction_instead_of_summing_means():
    controller, clock = moving_controller()
    frames(controller, clock, 22)
    state = frames(controller, clock, 7)
    assert state['scenario']['seats']['total'] == 7
    assert state['scenario']['seats']['camera_check']['last_mean'] == 7
    assert state['scenario']['seats']['confirmed_total'] == 5


def test_full_cabin_opt_out_prevents_camera_mean_from_changing_seats():
    controller, clock = moving_controller()
    controller.control('cabin_coverage', value=False)
    state = frames(controller, clock, 22)
    assert state['scenario']['seats']['total'] == 5
    assert state['scenario']['seats']['camera_check']['last_mean'] is None
