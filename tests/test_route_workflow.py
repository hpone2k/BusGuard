"""Five-stop/location contract and continuous seated departure evidence."""
import pytest
from types import SimpleNamespace

from vision.assistance import AssistanceController, AssistanceError
from vision.route import STOP_IDS
from timing_helpers import assume_clear_standing_check


class Clock:
    now = 1000.

    def __call__(self):
        return self.now


def setup():
    clock = Clock()
    controller = AssistanceController(clock=clock)
    assume_clear_standing_check(controller)
    controller.scenario_control('start')
    clock.now += .8
    controller.tick()
    return controller, clock


def body(key='one', stop='campus', journey='boarding', location=None):
    return dict(client_request_id=key, request_token='a' * 48, journey=journey,
                stop_id=stop, needs=['extra_time'], location=location if location is not None else
                {'mode': 'onboard'} if journey == 'alighting' else {'mode': 'at_stop', 'stop_id': stop})


def close(controller, clock):
    clock.now = controller.scenario.boarding_until - 6.2
    controller.tick()  # Deliver the departure warning at its scheduled instant.
    clock.now = controller.scenario.boarding_until
    assert controller.tick()['vehicle']['doors'] == 'closing'
    clock.now += .8
    assert controller.tick()['vehicle']['doors'] == 'closed'


def depart(controller, clock):
    close(controller, clock)
    clock.now += 3
    assert controller.tick()['vehicle']['motion'] == 'moving'


def test_route_stop_button_visits_every_stop_and_wraps_without_autostopping():
    c, clock = setup()
    assert [stop['name'] for stop in c.config()['stops']] == [f'Bus stop {letter}' for letter in 'ABCDE']
    event_ids = set()
    for target in [*STOP_IDS[1:], STOP_IDS[0]]:
        depart(c, clock)
        clock.now += 120
        travelling = c.tick()['route']
        assert travelling['phase'] == 'travelling'
        assert travelling['progress'] == .85
        assert travelling['next_stop_id'] == target
        old_event = travelling['arrival_event']
        # A stale dropdown selection cannot skip a stop in scenario mode.
        braking = c.scenario_control('stop', stop_id='stop_e')
        assert braking['vehicle']['stop_id'] == target
        assert braking['route']['next_stop_id'] == target
        assert braking['route']['phase'] == 'approaching'
        assert braking['route']['arrival_event'] == old_event
        assert 'Approaching Bus stop' in braking['announcements'][0]
        cycle = braking['scenario']['cycle_id']
        assert c.scenario_control('stop')['scenario']['cycle_id'] == cycle
        clock.now += 2
        arrived = c.tick()
        event = arrived['route']['arrival_event']
        assert event['stop_id'] == target and event['id'] not in event_ids
        event_ids.add(event['id'])
        assert arrived['route']['phase'] == 'at_stop'
        assert 'has arrived at Bus stop' in arrived['announcements'][0]
        clock.now += .8
        c.tick()


def test_early_stop_keeps_route_progress_continuous_while_braking():
    c, clock = setup()
    depart(c, clock)
    clock.now += 5
    prior = c.tick()['route']['progress']
    assert 0 < prior < .85
    assert c.scenario_control('stop')['route']['progress'] == prior
    clock.now += 1
    assert prior < c.tick()['route']['progress'] < 1


@pytest.mark.parametrize('location', [None, {}, {'mode': 'away'}, {'mode': 'onboard'},
                                     {'mode': 'at_stop', 'stop_id': 'interchange'},
                                     {'mode': 'at_stop', 'stop_id': 'campus', 'trusted': True}])
def test_location_rejection_does_not_create_request_or_extend_timer(location):
    c, clock = setup()
    clock.now += 8
    remaining = c.snapshot()['scenario']['boarding_remaining_ms']
    payload = body() | {'location': location}
    with pytest.raises(AssistanceError):
        c.create_request(payload)
    assert c.snapshot()['active_count'] == 0
    assert c.snapshot()['scenario']['boarding_remaining_ms'] == remaining


def test_alighting_requires_onboard_location_and_current_stop():
    c, _ = setup()
    with pytest.raises(AssistanceError, match='On board'):
        c.create_request(body(journey='alighting', location={'mode': 'at_stop', 'stop_id': 'campus'}))
    with pytest.raises(AssistanceError, match='selected stop'):
        c.create_request(body(journey='alighting', stop='interchange'))
    assert c.create_request(body(journey='alighting'))['status'] == 'awaiting_completion'


def test_existing_request_retry_without_location_survives_bus_departure():
    c, clock = setup()
    payload = body()
    item = c.create_request(payload)
    c.complete_request(item['id'], 'a' * 48)
    depart(c, clock)
    del payload['location']
    assert c.create_request(payload)['id'] == item['id']
    assert c.snapshot()['active_count'] == 0
    with pytest.raises(AssistanceError):
        c.create_request(payload | {'client_request_id': 'new'})


def test_last_activity_finishes_ten_seconds_after_fifty_second_admission_cutoff():
    c, clock = setup()
    opened = c.scenario.started_at
    for index, at in enumerate([9, 18, 27, 36, 45, 49]):
        clock.now = opened + at
        state = c.scenario_rfid(f'card-{index}', 'person')
        assert state['scenario']['boarding_remaining_ms'] == 10000
    late = c.create_request(body('late'))
    assert late['assistance_timer']['remaining_ms'] == 10000
    clock.now = opened + 50
    state = c.tick()
    assert state['vehicle']['doors'] == 'open'
    assert state['scenario']['finishing_extensions']
    with pytest.raises(AssistanceError):
        c.create_request(body('too-late'))
    with pytest.raises(AssistanceError):
        c.scenario_rfid('too-late', 'person')
    assert c.get_request(late['id'], 'a' * 48)['status'] == 'awaiting_completion'
    clock.now = opened + 59
    assert c.tick()['vehicle']['doors'] == 'closing'
    assert c.get_request(late['id'], 'a' * 48)['status'] == 'error'


@pytest.mark.parametrize('status', ['standing', 'unknown'])
def test_posture_holds_indefinitely_after_door_closure(status):
    c, clock = setup()
    c.scenario_control('load', priority_occupied=0, standard_occupied=1)
    c.posture_enabled = True
    # Isolate the scenario policy from the already-tested MediaPipe evidence parser.
    c.departure.result = lambda _: dict(can_depart=False, status=status, progress_ms=0,
                                        confirmation_ms=3000, expected_people=1, message='Please be seated.')
    close(c, clock)
    closed_at = c.scenario.closed_at
    warning_at = c.scenario.notice_at
    clock.now = closed_at + 3
    state = c.tick()
    assert state['vehicle']['motion'] == 'stationary'
    assert state['scenario']['posture_wait']['remaining_ms'] is None
    assert state['scenario']['departure_notice']['remaining_ms'] is None
    assert c.scenario.notice_at == warning_at
    # Elapsed time and source resets never bypass uncertain or standing evidence.
    c.departure.closed_at = clock.now + 100
    clock.now = closed_at + 9.99
    assert c.tick()['vehicle']['motion'] == 'stationary'
    clock.now = closed_at + 10
    held = c.tick()
    assert held['vehicle']['motion'] == 'stationary'
    assert held['scenario']['posture_wait'] == dict(enabled=True, remaining_ms=None,
                                                   confirmation_ms=3000, expired=False, bypassed=False, status='checking')
    clock.now += 50
    assert c.tick()['vehicle']['motion'] == 'stationary'


def test_three_second_seated_confirmation_permits_departure_and_then_monitors():
    c, clock = setup()
    c.posture_enabled = True
    c.scenario_control('load', priority_occupied=0, standard_occupied=1)
    close(c, clock)
    c.departure.result = lambda _: dict(can_depart=True, status='ready', progress_ms=3000, confirmation_ms=3000,
                                        expected_people=1, message='Seated.')
    clock.now += 3
    assert c.tick()['vehicle']['motion'] == 'moving'
    clock.now += 50
    assert c.tick()['scenario']['posture_wait']['status'] == 'monitoring'


@pytest.mark.parametrize('hold', ['emergency', 'obstruction', 'mobility', 'capacity'])
def test_waiting_longer_never_bypasses_other_holds(hold):
    c, clock = setup()
    c.posture_enabled = True
    c.scenario_control('load', priority_occupied=0, standard_occupied=1)
    close(c, clock)
    if hold in {'emergency', 'obstruction'}:
        c.control(hold, **({'value': True} if hold == 'obstruction' else {}))
    elif hold == 'mobility':
        c.vehicle.update(wheelchair_aboard=True, wheelchair_secured=False)
    else:
        # A completed cabin reconciliation remains authoritative after its
        # source frame ages; a manually assigned unconfirmed count does not.
        c.scenario.reconcile_camera(SimpleNamespace(mean=27., rounded=27, completed_at=clock()))
    clock.now += 60
    assert c.tick()['vehicle']['motion'] == 'stationary'


def test_restart_preserves_legacy_stop_ids_without_restoring_route_motion(tmp_path):
    c, clock = setup()
    c.path = tmp_path / 'state.json'
    depart(c, clock)
    c.scenario_control('stop')
    restored = AssistanceController(c.path, clock)
    assert restored.vehicle['stop_id'] == 'interchange'
    assert restored.vehicle['revalidation_required']
    assert restored.snapshot()['route']['arrival_event'] is None
    clock.now += 100
    assert restored.tick()['vehicle']['motion'] == 'stationary'
