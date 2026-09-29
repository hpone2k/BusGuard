"""Scenario time is controlled explicitly; none of these tests sleep."""
import json

import pytest

from vision.assistance import AssistanceController, AssistanceError
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
    controller = AssistanceController(tmp_path / 'scenario.json', clock)
    assume_clear_standing_check(controller)
    return controller, clock


def request(key='one', *, journey='boarding', needs=None, stop='campus'):
    return dict(client_request_id=key, request_token='a' * 48, journey=journey,
                stop_id=stop, needs=needs or ['extra_time'],
                location={'mode': 'onboard'} if journey == 'alighting' else {'mode': 'at_stop', 'stop_id': stop})


def start(controller, clock):
    result = controller.scenario_control('start')
    clock.advance(.8)
    controller.tick()
    return result


def travel(controller, clock):
    notice = controller.snapshot()['scenario']['departure_notice']
    if not notice['announced']:
        clock.advance(max(0, (notice['remaining_ms'] - 10000) / 1000))
        controller.tick()
    remaining = controller.snapshot()['scenario']['boarding_remaining_ms'] / 1000
    clock.advance(remaining)
    assert controller.tick()['vehicle']['doors'] == 'closing'
    clock.advance(.8)
    assert controller.tick()['vehicle']['doors'] == 'closed'
    clock.advance(3)
    state = controller.tick()
    # A caller may already have skipped over the scheduled warning instant.
    # Even then, the controller must provide ten seconds after it was spoken.
    remaining_notice = state['scenario']['departure_notice']['minimum_remaining_ms']
    if remaining_notice:
        clock.advance(remaining_notice / 1000)
        state = controller.tick()
    return state


def test_idle_stop_fifteen_seconds_then_close_then_full_three_second_wait(system):
    c, clock = system
    initial = start(c, clock)
    assert initial['scenario']['phase'] == 'opening'
    assert initial['scenario']['boarding_remaining_ms'] == 0
    assert c.snapshot()['scenario']['boarding_remaining_ms'] == 15000
    clock.advance(8.8)
    assert c.tick()['scenario']['departure_notice']['announced']
    clock.advance(6.19)
    assert c.tick()['vehicle']['doors'] == 'open'
    clock.advance(.01)
    assert c.tick()['vehicle']['doors'] == 'closing'
    clock.advance(.8)
    state = c.tick()
    assert state['scenario']['departure_remaining_ms'] == 3000
    assert state['scenario']['phase'] == 'departure_wait'
    clock.advance(2.99)
    assert c.tick()['vehicle']['motion'] == 'stationary'
    clock.advance(.01)
    state = c.tick()
    assert state['vehicle']['motion'] == 'moving'
    assert not state['scenario']['detection_enabled']
    clock.advance(100)
    assert c.tick()['vehicle']['motion'] == 'moving'


def test_only_stop_command_begins_next_cycle_and_duplicates_do_not_reset_it(system):
    c, clock = system
    start(c, clock)
    clock.advance(4)
    before = c.snapshot()['scenario']
    after = c.scenario_control('stop', stop_id='community')['scenario']
    assert after['boarding_remaining_ms'] == before['boarding_remaining_ms']
    assert c.vehicle['stop_id'] == 'campus'
    travel(c, clock)
    state = c.scenario_control('stop', stop_id='community')
    assert state['vehicle']['stop_id'] == 'interchange'
    assert state['scenario']['cycle_id'] == before['cycle_id'] + 1
    assert state['scenario']['phase'] == 'braking'
    assert state['scenario']['braking_remaining_ms'] == 2000
    assert state['vehicle']['doors'] == 'closed'
    clock.advance(2)
    assert c.tick()['scenario']['phase'] == 'opening'
    clock.advance(.8)
    assert c.tick()['scenario']['boarding_remaining_ms'] == 15000


def test_detection_generation_changes_only_on_gate_cycles(system):
    c, clock = system
    manual = c.detection_policy()
    start(c, clock)
    boarding = c.detection_policy()
    assert manual['generation'] != boarding['generation']
    c.scenario_control('board', seat_type='standard', event_id='boarding1')
    assert boarding == c.detection_policy()
    travel(c, clock)
    moving = c.detection_policy()
    assert not moving['enabled']
    assert moving['generation'] != boarding['generation']
    c.scenario_control('stop')
    assert not c.detection_policy()['enabled']
    clock.advance(2)
    c.tick()
    assert not c.detection_policy()['enabled']
    clock.advance(.8)
    c.tick()
    assert c.detection_policy()['enabled']
    assert c.detection_policy()['generation'] not in {moving['generation'], boarding['generation']}


def test_actual_activity_renews_ten_seconds_without_stacking_at_same_instant(system):
    c, clock = system
    start(c, clock)
    clock.advance(8.2)
    first = c.scenario_rfid('card-1', 'person', simulated=True)
    assert first['scenario']['boarding_remaining_ms'] == 10000
    second = c.scenario_rfid('card-2', 'senior', simulated=True)
    assert second['scenario']['boarding_remaining_ms'] == 10000
    assert second['scenario']['seats']['total'] == 2
    clock.advance(4)
    c.scenario_control('alight', seat_type='standard', event_id='exit-1')
    assert c.snapshot()['scenario']['boarding_remaining_ms'] == 10000


def test_request_extends_ten_seconds_once_and_reservation_is_not_boarding(system):
    c, clock = system
    start(c, clock)
    clock.advance(8.2)
    first = c.create_request(request())
    state = c.snapshot()['scenario']
    assert state['boarding_remaining_ms'] == 10000
    assert state['seats']['total'] == 0
    assert state['seats']['standard']['reserved'] == 1
    clock.advance(4)
    assert c.create_request(request())['id'] == first['id']
    assert c.snapshot()['scenario']['boarding_remaining_ms'] == 6000
    completed = c.complete_request(first['id'], 'a' * 48)
    assert completed['status'] == 'completed'
    assert c.snapshot()['scenario']['seats']['standard']['occupied'] == 0
    assert c.snapshot()['scenario']['seats']['standard']['reserved'] == 0
    c.complete_request(first['id'], 'a' * 48)
    assert c.snapshot()['scenario']['seats']['total'] == 0
    c.scenario_rfid('actual-boarding', 'person', simulated=True)
    assert c.snapshot()['scenario']['seats']['total'] == 1


def test_unconfirmed_request_expires_honestly_releases_seat_and_does_not_prevent_departure(system):
    c, clock = system
    start(c, clock)
    item = c.create_request(request())
    state = travel(c, clock)
    result = c.get_request(item['id'], 'a' * 48)
    assert result['status'] == 'error'
    assert 'not been counted as boarded' in result['message']
    assert state['scenario']['seats']['total'] == 0
    assert state['scenario']['seats']['reserved'] == 0
    assert state['vehicle']['motion'] == 'moving'


def test_repeated_requests_cannot_extend_admission_beyond_fifty_seconds(system):
    c, clock = system
    start(c, clock)
    for number, elapsed in enumerate((8, 16, 24, 32, 40, 48)):
        clock.now = c.scenario.started_at + elapsed
        c.create_request(request(f'request{number}'))
        assert c.scenario.boarding_until == c.scenario.started_at + elapsed + 10
    state = c.snapshot()['scenario']
    assert state['boarding_remaining_ms'] == 10000
    assert state['hard_remaining_ms'] == state['admission_remaining_ms'] == 2000
    clock.advance(2)
    state = c.tick()
    assert not state['scenario']['admission_open']
    assert state['active_count'] == 1
    assert state['vehicle']['doors'] == 'open'
    assert state['scenario']['finishing_extensions']
    assert state['scenario']['seats']['total'] == 0
    assert state['scenario']['seats']['reserved'] == 1
    with pytest.raises(AssistanceError):
        c.scenario_rfid('late-card', 'senior', simulated=True)
    with pytest.raises(AssistanceError):
        c.create_request(request('too-late'))
    clock.advance(8)
    state = c.tick()
    assert state['active_count'] == 0
    assert state['scenario']['seats']['reserved'] == 0
    assert state['vehicle']['doors'] == 'closing'


def test_each_request_expires_at_its_own_deadline_even_when_another_keeps_stop_open(system):
    c, clock = system
    start(c, clock)
    first = c.create_request(request())
    assert first['assistance_timer'] == dict(enabled=True, state='active', maximum_ms=10000,
                                            allowance_ms=10000, remaining_ms=10000)
    clock.advance(8)
    second = c.create_request(request('later'))
    assert c.get_request(first['id'], 'a' * 48)['assistance_timer']['remaining_ms'] == 2000
    clock.advance(2)
    state = c.tick()
    expired = c.get_request(first['id'], 'a' * 48)
    assert expired['status'] == 'error'
    assert expired['assistance_timer']['state'] == 'expired'
    assert expired['assistance_timer']['remaining_ms'] == 0
    assert state['scenario']['boarding_remaining_ms'] == 8000
    assert state['scenario']['seats']['reserved'] == 1
    assert c.get_request(second['id'], 'a' * 48)['assistance_timer']['remaining_ms'] == 8000
    with pytest.raises(AssistanceError):
        c.complete_request(first['id'], 'a' * 48)


def test_request_completion_does_not_replenish_private_or_stop_allowance(system):
    c, clock = system
    start(c, clock)
    item = c.create_request(request())
    clock.advance(9)
    assert c.get_request(item['id'], 'a' * 48)['assistance_timer']['remaining_ms'] == 1000
    done = c.complete_request(item['id'], 'a' * 48)
    assert done['assistance_timer']['state'] == 'completed'
    assert c.snapshot()['scenario']['boarding_remaining_ms'] == 6000


def test_late_request_receives_full_allowance_after_admission_cutoff(system):
    c, clock = system
    start(c, clock)
    for number, elapsed in enumerate((8, 16, 24, 32, 40)):
        clock.now = c.scenario.started_at + elapsed
        c.create_request(request(f'keep{number}'))
    clock.now = c.scenario.started_at + 48
    item = c.create_request(request('last'))
    assert item['assistance_timer']['allowance_ms'] == 10000
    assert item['assistance_timer']['remaining_ms'] == 10000
    clock.advance(2)
    assert c.get_request(item['id'], 'a' * 48)['assistance_timer']['remaining_ms'] == 8000
    assert not c.snapshot()['scenario']['admission_open']
    clock.advance(8)
    assert c.get_request(item['id'], 'a' * 48)['assistance_timer']['state'] == 'expired'


@pytest.mark.parametrize('needs,allowance', [(['extra_time'], 10), (['ramp'], 20)])
def test_last_second_accepted_request_can_complete_after_admission_cutoff(system, needs, allowance):
    c, clock = system
    start(c, clock)
    for index, elapsed in enumerate((9, 18, 27, 36, 45)):
        clock.now = c.scenario.started_at + elapsed
        c.scenario_rfid(f'keep:{index}', 'person', simulated=True)
    clock.now = c.scenario.started_at + 49
    item = c.create_request(request('last', needs=needs))
    assert item['assistance_timer']['remaining_ms'] == allowance * 1000
    assert item['assistance_timer']['maximum_ms'] == allowance * 1000
    assert c.scenario.boarding_until == c.scenario.started_at + 49 + allowance
    deadline = c.scenario.boarding_until
    clock.advance(1)
    state = c.tick()
    assert state['scenario']['finishing_extensions'] and not state['scenario']['admission_open']
    assert state['vehicle']['doors'] == 'open'
    assert c.get_request(item['id'], 'a' * 48)['assistance_timer']['remaining_ms'] == (allowance - 1) * 1000
    with pytest.raises(AssistanceError, match='Requests are closed'):
        c.create_request(request('rejected', needs=needs))
    clock.advance(1)
    completed = c.complete_request(item['id'], 'a' * 48)
    assert completed['status'] == 'completed'
    assert c.scenario.boarding_until == deadline
    clock.now = deadline - .001
    assert c.tick()['vehicle']['doors'] == 'open'
    clock.now = deadline
    state = c.tick()
    if needs == ['ramp']:
        assert state['vehicle']['ramp'] == 'stowing'
        clock.advance(c.MOVE_SECONDS)
        state = c.tick()
    assert state['vehicle']['doors'] == 'closing'


def test_ramp_request_gets_twenty_seconds_and_later_scan_cannot_shorten_it(system):
    c, clock = system
    start(c, clock)
    clock.advance(8)
    item = c.create_request(request(needs=['ramp']))
    assert item['assistance_timer']['allowance_ms'] == 20000
    assert c.snapshot()['scenario']['boarding_remaining_ms'] == 20000
    deadline = c.scenario.boarding_until
    clock.advance(1)
    c.scenario_rfid('later-scan', 'person', simulated=True)
    assert c.scenario.boarding_until == deadline
    assert c.get_request(item['id'], 'a' * 48)['assistance_timer']['remaining_ms'] == 19000


def test_braking_and_opening_reject_new_requests_until_doors_open(system):
    c, clock = system
    start(c, clock)
    travel(c, clock)
    with pytest.raises(AssistanceError):
        c.create_request(request(stop='interchange'))
    state = c.scenario_control('stop', stop_id='community')
    assert state['scenario']['motion']['phase'] == 'braking'
    assert state['scenario']['motion']['duration_ms'] == 2000
    clock.advance(1)
    repeated = c.scenario_control('stop', stop_id='interchange')
    assert repeated['vehicle']['stop_id'] == 'interchange'
    assert repeated['scenario']['braking_remaining_ms'] == 1000
    with pytest.raises(AssistanceError):
        c.create_request(request(stop='interchange'))
    clock.advance(1)
    c.tick()
    with pytest.raises(AssistanceError):
        c.create_request(request(stop='interchange'))
    clock.advance(.8)
    c.tick()
    assert c.create_request(request(stop='interchange'))['assistance_timer']['remaining_ms'] == 10000


def test_future_requests_rejected_while_moving_and_only_current_stop_can_reserve(system):
    c, clock = system
    start(c, clock)
    travel(c, clock)
    with pytest.raises(AssistanceError):
        c.create_request(request(stop='community'))
    assert c.snapshot()['scenario']['seats']['reserved'] == 0
    c.scenario_control('stop', stop_id='interchange')
    assert c.snapshot()['active_count'] == 0
    clock.advance(2)
    c.tick()
    clock.advance(.8)
    c.tick()
    travel(c, clock)
    state = c.scenario_control('stop', stop_id='community')
    assert state['scenario']['seats']['reserved'] == 0
    clock.advance(2)
    c.tick()
    clock.advance(.8)
    state = c.tick()
    assert state['scenario']['seats']['reserved'] == 0
    assert c.create_request(request(stop='community'))['status'] == 'awaiting_completion'
    assert c.snapshot()['scenario']['seats']['reserved'] == 1


def test_full_bus_rejects_rfid_with_announcement_and_no_extra_delay(system):
    c, clock = system
    start(c, clock)
    c.scenario_control('load', priority_occupied=6, standard_occupied=20)
    before = c.snapshot()['scenario']['boarding_remaining_ms']
    result = c.scenario_rfid('full-card', 'senior', simulated=True)['scenario']
    assert result['seats']['total'] == 26
    assert not result['last_admission']['allowed']
    assert result['boarding_remaining_ms'] == before
    assert 'Please wait for the next bus' in result['announcement']['message']
    identifier = result['announcement']['id']
    assert c.snapshot()['scenario']['announcement']['id'] == identifier
    assert c.scenario_rfid('full-card', 'senior', simulated=True)['scenario']['announcement']['id'] == identifier
    assert c.scenario_rfid('full-card-2', 'senior', simulated=True)['scenario']['announcement']['id'] != identifier
    alight = c.scenario_control('alight', seat_type='standard', event_id='full-exit')['scenario']
    assert alight['last_admission']['allowed']
    assert alight['seats']['total'] == 25


def test_ordinary_passengers_can_use_unused_priority_seats(system):
    c, clock = system
    start(c, clock)
    c.scenario_control('load', priority_occupied=0, standard_occupied=20)
    state = c.scenario_rfid('ordinary-overflow', 'person', simulated=True)['scenario']
    assert state['seats']['priority']['occupied'] == 1
    assert state['seats']['priority']['non_priority_occupied'] == 1
    assert state['seats']['total'] == 21


def test_priority_passenger_prompts_simulated_reassignment_without_changing_other_occupancy(system):
    c, clock = system
    start(c, clock)
    c.scenario_control('load', priority_occupied=6, standard_occupied=19, priority_regular=1)
    state = c.scenario_rfid('senior-yield', 'senior', simulated=True)['scenario']
    assert state['seats']['priority']['occupied'] == 6
    assert state['seats']['standard']['occupied'] == 20
    assert state['seats']['priority']['non_priority_occupied'] == 0
    assert state['seats']['total'] == 26
    assert 'offer the priority seat' in state['announcement']['message']


def test_pending_boarding_reserves_last_seat_then_cancellation_releases_it(system):
    c, clock = system
    start(c, clock)
    c.scenario_control('load', priority_occupied=6, standard_occupied=19)
    reserved = c.create_request(request())
    assert c.snapshot()['scenario']['seats']['available'] == 0
    rejected = c.create_request(request('no-room'))
    assert rejected['status'] == 'error'
    assert c.snapshot()['scenario']['seats']['total'] == 25
    assert not c.scenario_rfid('reserved-card', 'senior', simulated=True)['scenario']['last_admission']['allowed']
    c.cancel_request(reserved['id'], 'a' * 48)
    assert c.snapshot()['scenario']['seats']['available'] == 1


def test_alighting_request_completion_waits_for_scan_before_changing_count(system):
    c, clock = system
    start(c, clock)
    c.scenario_control('load', priority_occupied=6, standard_occupied=20)
    item = c.create_request(request(journey='alighting'))
    assert item['status'] == 'awaiting_completion'
    c.complete_request(item['id'], 'a' * 48)
    c.complete_request(item['id'], 'a' * 48)
    assert c.snapshot()['scenario']['seats']['total'] == 26
    c.scenario_control('alight', seat_type='standard', event_id='actual-exit')
    assert c.snapshot()['scenario']['seats']['total'] == 25


def test_rfid_retry_deduplicates_and_conflicting_event_is_rejected(system):
    c, clock = system
    start(c, clock)
    c.scenario_rfid('same-card', 'person', simulated=True)
    clock.advance(1)
    before = c.snapshot()['scenario']['boarding_remaining_ms']
    c.scenario_rfid('same-card', 'person', simulated=True)
    assert c.snapshot()['scenario']['seats']['total'] == 1
    assert c.snapshot()['scenario']['boarding_remaining_ms'] == before
    with pytest.raises(AssistanceError):
        c.scenario_rfid('same-card', 'senior', simulated=True)


def test_obstruction_holds_at_cap_then_closes_without_reopening_admission(system):
    c, clock = system
    start(c, clock)
    c.control('obstruction', value=True)
    clock.advance(60)
    state = c.tick()
    assert state['scenario']['phase'] == 'held'
    assert state['vehicle']['doors'] == 'open'
    assert not state['scenario']['admission_open']
    c.control('obstruction', value=False)
    assert c.vehicle['doors'] == 'closing'
    clock.advance(.8)
    c.tick()
    clock.advance(3)
    state = c.tick()
    assert state['vehicle']['motion'] == 'stationary'
    remaining = state['scenario']['departure_notice']['minimum_remaining_ms']
    assert remaining > 0
    clock.advance(remaining / 1000)
    assert c.tick()['vehicle']['motion'] == 'moving'


def test_obstruction_during_closure_requires_new_closure_then_full_wait(system):
    c, clock = system
    start(c, clock)
    clock.now = c.scenario.started_at + 15
    c.tick()
    clock.advance(.4)
    c.control('obstruction', value=True)
    assert c.vehicle['doors'] == 'open'
    clock.advance(100)
    c.control('obstruction', value=False)
    clock.advance(.8)
    assert c.tick()['scenario']['departure_remaining_ms'] == 3000


def test_posture_and_mobility_interlocks_survive_timed_expiry(system):
    c, clock = system
    use_real_standing_check(c)
    start(c, clock)
    c.scenario_control('load', priority_occupied=0, standard_occupied=1)
    c.posture_enabled = True
    state = travel(c, clock)
    assert state['vehicle']['motion'] == 'stationary'
    assert state['scenario']['phase'] == 'held'
    assert not state['scenario']['detection_enabled']
    assert state['scenario']['inside_detection_enabled']
    assert state['announcements'][0] == state['readiness']['message']
    assert not c.detection_policy()['enabled']
    assert c.detection_policy('inside')['enabled']
    assume_clear_standing_check(c)
    c.vehicle.update(wheelchair_aboard=True, wheelchair_secured=False)
    assert c.tick()['vehicle']['motion'] == 'stationary'
    state = c.control('secure_wheelchair', value=True)
    assert state['vehicle']['motion'] == 'stationary'
    assert state['scenario']['departure_notice']['minimum_remaining_ms'] == 10000
    clock.advance(10)
    assert c.tick()['vehicle']['motion'] == 'moving'


def test_emergency_and_restart_never_resume_auto_travel(system):
    c, clock = system
    start(c, clock)
    c.scenario_rfid('first-card', 'person', simulated=True)
    travel(c, clock)
    restored = AssistanceController(c.path, clock)
    state = restored.snapshot()
    assert state['scenario']['enabled']
    assert state['scenario']['phase'] == 'held'
    assert state['vehicle']['motion'] == 'stationary'
    assert state['scenario']['seats']['total'] == 1
    clock.advance(100)
    assert restored.tick()['vehicle']['motion'] == 'stationary'
    with pytest.raises(AssistanceError):
        restored.scenario_control('start')
    restored.scenario_control('reset')
    clock.advance(.8)
    restored.tick()
    restored.scenario_rfid('first-card', 'person', simulated=True)
    assert restored.snapshot()['scenario']['seats']['total'] == 1
    restored.control('emergency')
    clock.advance(100)
    assert restored.tick()['vehicle']['motion'] == 'stationary'


def test_late_current_stop_request_is_rejected_without_blocking_departure(system):
    c, clock = system
    start(c, clock)
    clock.now = c.scenario.started_at + 15
    c.tick()
    with pytest.raises(AssistanceError, match='Requests are closed'):
        c.create_request(request())
    assert c.snapshot()['active_count'] == 0


def test_stale_sensor_events_and_closed_window_events_cannot_create_requests(system):
    c, clock = system
    base = dict(instance_id='bridge', sources={}, events=[])
    c.tick(base)
    clock.advance(2)
    start(c, clock)
    old = dict(id=1, type='wheelchair', origin='vision', source_kind='live', source_role='outside',
               timestamp_ms=1001000, is_demo=False)
    assert c.tick(base | {'events': [old]})['active_count'] == 0
    clock.now = c.scenario.started_at + 15
    c.tick()
    recent = old | dict(id=2, timestamp_ms=clock() * 1000)
    assert c.tick(base | {'events': [recent]})['active_count'] == 0


@pytest.mark.parametrize('priority,standard,ordinary', [(-1, 0, 0), (7, 0, 0), (0, 21, 0),
                                                     (True, 0, 0), (2, 0, 3), (2, 0, -1)])
def test_load_rejects_invalid_counts_without_mutation(system, priority, standard, ordinary):
    c, clock = system
    start(c, clock)
    with pytest.raises(AssistanceError):
        c.scenario_control('load', priority_occupied=priority, standard_occupied=standard,
                           priority_regular=ordinary)
    assert c.snapshot()['scenario']['seats']['total'] == 0


def test_invalid_durable_seat_ledger_is_held_and_preserved(system):
    c, clock = system
    start(c, clock)
    data = json.loads(c.path.read_text())
    data['scenario']['occupied']['priority'] = 30
    raw = json.dumps(data)
    c.path.write_text(raw)
    restored = AssistanceController(c.path, clock)
    assert restored.snapshot()['vehicle']['emergency']
    assert c.path.read_text() == raw
