import json

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from vision.assistance import AssistanceController, AssistanceError
from vision.assistance_api import assistance_router


class Clock:
    def __init__(self):
        self.now = 1000.

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def payload(key='request-1', token='a' * 48, **changes):
    body = dict(client_request_id=key, request_token=token, journey='boarding',
                stop_id='campus', needs=['extra_time']) | changes
    body.setdefault('location', {'mode': 'onboard'} if body['journey'] == 'alighting' else
                    {'mode': 'at_stop', 'stop_id': body['stop_id']})
    return body


@pytest.fixture
def system(tmp_path):
    clock = Clock()
    return AssistanceController(tmp_path / 'assistance.json', clock), clock


def ready_access(controller, clock):
    clock.advance(2)
    controller.tick()


@pytest.mark.parametrize('needs', [['ramp', 'extra_time'], ['audio', 'visual'], [], ['ramp', 'ramp']])
def test_new_passenger_request_requires_one_option_without_mutating_state(system, needs):
    controller, _ = system
    before = controller.snapshot()
    with pytest.raises(AssistanceError, match='one assistance option'):
        controller.create_request(payload(needs=needs))
    assert controller.snapshot() == before
    assert controller.requests == {}


def test_old_multi_option_request_retries_keep_the_original_receipt(system):
    controller, _ = system
    item = controller.create_request(payload(needs=['ramp']))
    # Model a journal entry accepted before passenger choices became exclusive.
    controller.requests[item['id']]['needs'] = ['ramp', 'visual']
    original = controller.get_request(item['id'], 'a' * 48)
    retried = controller.create_request(payload(needs=['ramp', 'visual']))
    assert retried['id'] == original['id']
    assert retried['needs'] == ['ramp', 'visual']
    assert len(controller.requests) == 1


def finish_movements(controller, clock):
    clock.advance(10)
    controller.tick()
    clock.advance(2)
    controller.tick()


def test_passenger_confirmation_not_timer_completes_request(system):
    controller, clock = system
    created = controller.create_request(payload())
    assert created['status'] == 'assisting'
    clock.advance(3600)
    state = controller.tick()
    assert state['vehicle']['doors'] == 'open'
    assert controller.get_request(created['id'], 'a' * 48)['status'] == 'awaiting_completion'
    assert not state['readiness']['can_depart']
    response = controller.complete_request(created['id'], 'a' * 48)
    assert response['status'] == 'awaiting_completion'
    clock.advance(2)
    controller.tick()
    assert controller.get_request(created['id'], 'a' * 48)['status'] == 'completed'
    assert controller.snapshot()['readiness']['can_depart']


def test_all_passengers_confirm_and_minimum_dwell_is_respected(system):
    controller, clock = system
    first = controller.create_request(payload())
    second = controller.create_request(payload('request-2', 'b' * 48))
    ready_access(controller, clock)
    controller.complete_request(first['id'], 'a' * 48)
    clock.advance(12)
    assert controller.tick()['vehicle']['doors'] == 'open'
    controller.complete_request(second['id'], 'b' * 48)
    assert controller.snapshot()['vehicle']['doors'] == 'closing'
    clock.advance(1)
    assert controller.tick()['vehicle']['doors'] == 'closed'


def test_confirm_early_holds_until_minimum_dwell(system):
    controller, clock = system
    item = controller.create_request(payload())
    ready_access(controller, clock)
    controller.complete_request(item['id'], 'a' * 48)
    clock.advance(3)
    assert controller.tick()['vehicle']['doors'] == 'open'
    assert controller.snapshot()['readiness']['minimum_dwell_remaining_ms'] == 3000
    clock.advance(3)
    assert controller.tick()['vehicle']['doors'] == 'closing'


def test_future_alighting_request_is_rejected_until_selected_stop(system):
    controller, clock = system
    with pytest.raises(AssistanceError, match='selected stop'):
        controller.create_request(payload(journey='alighting', stop_id='community'))
    controller.control('depart')
    clock.advance(50)
    assert controller.tick()['vehicle']['phase'] == 'travelling'
    controller.control('arrive', stop_id='interchange')
    assert controller.snapshot()['active_count'] == 0
    controller.control('depart')
    controller.control('arrive', stop_id='community')
    assert controller.create_request(payload(journey='alighting', stop_id='community'))['status'] == 'assisting'


def test_arrival_cannot_bypass_departure_hold(system):
    controller, clock = system
    item = controller.create_request(payload())
    with pytest.raises(AssistanceError):
        controller.control('arrive', stop_id='community')
    assert controller.snapshot()['vehicle']['stop_id'] == 'campus'
    controller.cancel_request(item['id'], 'a' * 48)
    finish_movements(controller, clock)
    with pytest.raises(AssistanceError):
        controller.control('arrive', stop_id='community')
    controller.control('depart')
    assert controller.control('arrive', stop_id='community')['vehicle']['stop_id'] == 'community'


def test_repeated_arrival_at_same_stop_preserves_assistance_and_confirmation(system):
    controller, clock = system
    item = controller.create_request(payload())
    ready_access(controller, clock)
    controller.complete_request(item['id'], 'a' * 48)
    before = controller.snapshot()
    after = controller.control('arrive', stop_id='campus')
    assert before['vehicle'] == after['vehicle']
    assert before['revision'] == after['revision']
    assert controller.get_request(item['id'], 'a' * 48)['passenger_confirmed']
    controller.control('emergency')
    assert controller.control('arrive', stop_id='campus')['vehicle']['emergency']


def test_request_for_previous_stop_during_travel_is_rejected(system):
    controller, _ = system
    controller.control('depart')
    with pytest.raises(AssistanceError, match='selected stop'):
        controller.create_request(payload())
    controller.control('arrive', stop_id='community')
    assert controller.snapshot()['active_count'] == 0
    controller.control('depart')
    controller.control('arrive', stop_id='campus')
    assert controller.create_request(payload())['status'] == 'assisting'


def test_ramp_stows_before_doors_close_and_securement_is_explicit(system):
    controller, clock = system
    item = controller.create_request(payload(needs=['ramp']))
    ready_access(controller, clock)
    assert controller.snapshot()['vehicle']['ramp'] == 'deployed'
    controller.complete_request(item['id'], 'a' * 48)
    clock.advance(2)
    state = controller.tick()
    assert state['vehicle']['ramp'] == 'stowing'
    assert state['vehicle']['doors'] == 'open'
    clock.advance(1)
    state = controller.tick()
    assert state['vehicle']['ramp'] == 'stowed'
    assert state['vehicle']['doors'] == 'closing'
    clock.advance(1)
    assert not controller.tick()['readiness']['can_depart']
    state = controller.control('secure_wheelchair', value=True)
    assert state['readiness']['can_depart']


def test_one_ramp_alighting_never_clears_other_mobility_requests(system):
    controller, clock = system
    first = controller.create_request(payload('ramp-1', needs=['ramp']))
    second = controller.create_request(payload('ramp-2', 'b' * 48, needs=['ramp']))
    ready_access(controller, clock)
    controller.complete_request(first['id'], 'a' * 48)
    controller.complete_request(second['id'], 'b' * 48)
    finish_movements(controller, clock)
    state = controller.snapshot()
    assert state['mobility_area']['request_balance'] == 2
    assert state['mobility_area']['review_required']
    controller.control('secure_wheelchair', value=True)
    controller.control('depart')
    controller.control('arrive', stop_id='community')
    exit_request = controller.create_request(payload('exit-1', 'c' * 48, needs=['ramp'],
                                                     journey='alighting', stop_id='community'))
    assert controller.snapshot()['mobility_area']['review_required']
    ready_access(controller, clock)
    controller.complete_request(exit_request['id'], 'c' * 48)
    finish_movements(controller, clock)
    state = controller.snapshot()
    assert state['mobility_area']['request_balance'] == 1
    assert state['vehicle']['wheelchair_aboard']
    assert not state['vehicle']['wheelchair_secured']
    assert not state['readiness']['can_depart']
    assert controller.control('secure_wheelchair', value=True)['readiness']['can_depart']


def test_alighting_without_matched_boarding_still_needs_area_review(system):
    controller, clock = system
    item = controller.create_request(payload(journey='alighting', needs=['ramp']))
    ready_access(controller, clock)
    controller.complete_request(item['id'], 'a' * 48)
    finish_movements(controller, clock)
    state = controller.snapshot()
    assert state['mobility_area']['request_balance'] == 0
    assert state['mobility_area']['review_required']
    assert not state['readiness']['can_depart']
    assert controller.control('secure_wheelchair', value=True)['readiness']['can_depart']


def test_confirmation_during_ramp_movement_is_invalidated_at_completion(system):
    controller, clock = system
    item = controller.create_request(payload(needs=['ramp']))
    ready_access(controller, clock)
    controller.control('secure_wheelchair', value=True)
    controller.complete_request(item['id'], 'a' * 48)
    finish_movements(controller, clock)
    assert controller.snapshot()['mobility_area']['review_required']


def test_mobility_ledger_is_bounded_and_restart_does_not_verify_area(system):
    controller, clock = system
    controller.vehicle['mobility_request_balance'] = controller.MAX_ACTIVE
    item = controller.create_request(payload(needs=['ramp']))
    ready_access(controller, clock)
    controller.complete_request(item['id'], 'a' * 48)
    finish_movements(controller, clock)
    controller.control('secure_wheelchair', value=True)
    restored = AssistanceController(controller.path, clock)
    state = restored.snapshot()
    assert state['mobility_area']['request_balance'] == controller.MAX_ACTIVE
    assert state['mobility_area']['review_required']
    assert state['vehicle']['revalidation_required']


def test_obstruction_blocks_closure_and_departure(system):
    controller, clock = system
    item = controller.create_request(payload(needs=['ramp']))
    ready_access(controller, clock)
    controller.control('obstruction', value=True)
    controller.complete_request(item['id'], 'a' * 48)
    clock.advance(100)
    assert controller.tick()['vehicle']['ramp'] == 'deployed'
    with pytest.raises(AssistanceError):
        controller.control('depart')
    controller.control('obstruction', value=False)
    assert controller.snapshot()['vehicle']['ramp'] == 'stowing'


def test_obstruction_during_closure_reopens_access(system):
    controller, clock = system
    item = controller.create_request(payload(needs=['visual']))
    ready_access(controller, clock)
    controller.complete_request(item['id'], 'a' * 48)
    clock.advance(3)
    assert controller.tick()['vehicle']['doors'] == 'closing'
    state = controller.control('obstruction', value=True)
    assert state['vehicle']['doors'] == 'open'
    clock.advance(30)
    assert controller.tick()['vehicle']['doors'] == 'open'


def test_emergency_latches_and_reset_invalidates_old_confirmation(system):
    controller, clock = system
    item = controller.create_request(payload())
    ready_access(controller, clock)
    controller.complete_request(item['id'], 'a' * 48)
    controller.control('emergency')
    clock.advance(100)
    assert controller.tick()['vehicle']['emergency']
    with pytest.raises(AssistanceError):
        controller.control('depart')
    controller.control('reset')
    assert not controller.get_request(item['id'], 'a' * 48)['passenger_confirmed']
    ready_access(controller, clock)
    assert controller.get_request(item['id'], 'a' * 48)['can_complete']


def test_restart_preserves_request_and_fails_held(system):
    controller, clock = system
    item = controller.create_request(payload())
    ready_access(controller, clock)
    controller.complete_request(item['id'], 'a' * 48)
    restored = AssistanceController(controller.path, clock)
    state = restored.snapshot()
    assert state['vehicle']['revalidation_required']
    assert state['vehicle']['doors'] == 'unknown'
    assert not state['readiness']['can_depart']
    assert restored.get_request(item['id'], 'a' * 48)['status'] == 'queued'
    assert not restored.get_request(item['id'], 'a' * 48)['passenger_confirmed']
    restored.control('reset')
    assert restored.get_request(item['id'], 'a' * 48)['status'] == 'assisting'


def test_corrupt_journal_is_preserved_and_held(tmp_path):
    path = tmp_path / 'broken.json'
    path.write_text('broken', encoding='utf-8')
    controller = AssistanceController(path)
    assert controller.snapshot()['vehicle']['emergency']
    assert path.read_text(encoding='utf-8') == 'broken'
    with pytest.raises(AssistanceError):
        controller.control('reset')
    with pytest.raises(AssistanceError) as caught:
        controller.create_request(payload())
    assert caught.value.status == 503


def test_idempotent_retry_needs_same_token_and_same_payload(system):
    controller, _ = system
    first = controller.create_request(payload())
    assert controller.create_request(payload())['id'] == first['id']
    assert len(controller.requests) == 1
    with pytest.raises(AssistanceError) as bad_token:
        controller.create_request(payload(token='b' * 48))
    assert bad_token.value.status == 409
    with pytest.raises(AssistanceError):
        controller.create_request(payload(needs=['ramp']))


@pytest.mark.parametrize('method', ['get_request', 'complete_request', 'cancel_request'])
def test_private_request_cannot_be_read_or_changed_without_owner_token(system, method):
    controller, _ = system
    item = controller.create_request(payload())
    with pytest.raises(AssistanceError) as caught:
        getattr(controller, method)(item['id'], 'b' * 48)
    assert caught.value.status == 404


def test_public_snapshot_and_journal_never_expose_raw_token(system):
    controller, _ = system
    item = controller.create_request(payload())
    raw = json.dumps(controller.snapshot())
    assert 'a' * 48 not in raw
    assert item['id'] not in raw
    assert 'request-1' not in raw
    assert 'a' * 48 not in controller.path.read_text(encoding='utf-8')
    assert '_token_hash' not in item


def test_cancellation_is_idempotent_and_cannot_complete_cancelled_request(system):
    controller, clock = system
    item = controller.create_request(payload())
    assert controller.cancel_request(item['id'], 'a' * 48)['status'] == 'cancelled'
    assert controller.cancel_request(item['id'], 'a' * 48)['status'] == 'cancelled'
    with pytest.raises(AssistanceError):
        controller.complete_request(item['id'], 'a' * 48)
    finish_movements(controller, clock)
    assert controller.snapshot()['vehicle']['doors'] == 'closed'


def source_snapshot(clock, frame=1, **changes):
    source = dict(session_id='live-1', frame_id=frame, kind='live', connected=True, age_ms=0,
                  posture_enabled=True, is_demo=False,
                  received_at_ms=clock() * 1000,
                  count_summary={'classes': {'person': {'status': 'stable', 'stable': 2}}},
                  seating_summary=dict(status='observed', age_ms=0, complete=True, people=2,
                                       seated=2, standing=0, unknown=0, captured_at_ms=clock() * 1000,
                                       occupants=[{'track_id': 1, 'posture': 'seated'},
                                                  {'track_id': 2, 'posture': 'seated'}]))
    source.update(changes)
    return dict(instance_id='bridge-1', server_time_ms=clock() * 1000,
                sources={'inside': source}, events=[])


def test_posture_gate_requires_successive_live_frames_and_expires(system):
    controller, clock = system
    for frame in range(10):
        controller.tick(source_snapshot(clock, frame))
        clock.advance(.4)
    assert controller.snapshot()['readiness']['can_depart']
    clock.advance(1)
    assert not controller.snapshot()['readiness']['can_depart']


def test_polling_same_frame_never_confirms_seating(system):
    controller, clock = system
    snapshot = source_snapshot(clock)
    for _ in range(10):
        controller.tick(snapshot)
        clock.advance(.3)
    assert not controller.snapshot()['readiness']['can_depart']


@pytest.mark.parametrize('changes', [dict(kind='image'), dict(kind='video'), dict(is_demo=True),
                                     dict(connected=False), dict(age_ms=1001),
                                     dict(seating_summary=dict(status='observed', age_ms=0, complete=True,
                                                               people=2, seated=1, standing=1, unknown=0)),
                                     dict(seating_summary=dict(status='observed', age_ms=0, complete=True,
                                                               people=2, seated=1, standing=0, unknown=1))])
def test_unreliable_posture_never_clears_departure(system, changes):
    controller, clock = system
    for frame in range(8):
        controller.tick(source_snapshot(clock, frame, **changes))
        clock.advance(.4)
    assert not controller.snapshot()['readiness']['can_depart']


def test_posture_checkbox_false_disables_optional_gate(system):
    controller, clock = system
    controller.tick(source_snapshot(clock, connected=False))
    assert not controller.snapshot()['readiness']['can_depart']
    controller.tick(source_snapshot(clock, connected=False, posture_enabled=False))
    assert controller.snapshot()['readiness']['can_depart']


def test_sensor_events_are_live_only_deduplicated_and_require_confirmation(system):
    controller, clock = system
    base = dict(instance_id='bridge-1', sources={}, events=[])
    controller.tick(base)
    for identifier, kind in [(1, 'image'), (2, 'video'), (3, 'live'), (4, 'live')]:
        event = dict(id=identifier, type='wheelchair', origin='vision', source_role='outside',
                     source_kind=kind, timestamp_ms=clock() * 1000, is_demo=False)
        controller.tick(base | {'events': [event]})
    assert len(controller.requests) == 1
    ready_access(controller, clock)
    clock.advance(100)
    assert controller.tick()['sensor_confirmation_count'] == 1
    controller.control('confirm_sensor_requests')
    clock.advance(2)
    assert controller.tick()['active_count'] == 0


def test_rfid_requests_extra_time_and_old_events_are_not_replayed(system):
    controller, clock = system
    event = dict(id=1, type='senior', origin='rfid', source_kind='rfid',
                 timestamp_ms=clock() * 1000)
    snapshot = dict(instance_id='bridge-1', sources={}, events=[event])
    assert controller.tick(snapshot)['active_count'] == 0
    event = event | {'id': 2}
    state = controller.tick(snapshot | {'events': [event]})
    assert state['active_count'] == 1
    assert state['requests'][0]['needs'] == ['extra_time', 'priority_seat']
    assert controller.tick(snapshot | {'events': [event]})['active_count'] == 1


def test_rounded_rfid_timestamp_does_not_drop_a_just_received_event(system):
    controller, clock = system
    base = dict(instance_id='bridge-1', sources={}, events=[])
    controller.tick(base)
    event = dict(id=1, type='senior', origin='rfid', source_kind='rfid',
                 timestamp_ms=clock() * 1000 + .04)
    assert controller.tick(base | {'events': [event]})['active_count'] == 1


def test_new_request_during_closure_reopens_before_servicing(system):
    controller, clock = system
    first = controller.create_request(payload(needs=['visual']))
    ready_access(controller, clock)
    controller.complete_request(first['id'], 'a' * 48)
    clock.advance(2)
    assert controller.tick()['vehicle']['doors'] == 'closing'
    second = controller.create_request(payload('later', 'b' * 48, needs=['ramp']))
    assert controller.snapshot()['vehicle']['doors'] == 'opening'
    ready_access(controller, clock)
    assert controller.get_request(second['id'], 'b' * 48)['can_complete']
    assert not controller.get_request(first['id'], 'a' * 48)['can_complete']


def test_active_queue_is_bounded(system):
    controller, _ = system
    controller.MAX_ACTIVE = 2
    controller.create_request(payload())
    controller.create_request(payload('second'))
    with pytest.raises(AssistanceError) as caught:
        controller.create_request(payload('third'))
    assert caught.value.status == 429


def test_router_enforces_operator_access_and_private_tokens(system):
    controller, clock = system

    class Bridge:
        def snapshot(self):
            return dict(instance_id='bridge', sources={}, events=[])

    def operator(x_operator_token: str | None = Header(default=None)):
        if x_operator_token != 'paired':
            raise HTTPException(status_code=403, detail='Pair this operator device first.')

    app = FastAPI()
    app.include_router(assistance_router(controller, Bridge(), operator))
    with TestClient(app) as client:
        assert client.get('/api/assistance/config').json()['simulation']
        created = client.post('/api/assistance/requests', json=payload())
        assert created.status_code == 200
        route = '/api/assistance/requests/' + created.json()['id']
        assert client.get(route).status_code == 404
        assert client.get(route, headers={'X-Request-Token': 'a' * 48}).status_code == 200
        assert client.post('/api/assistance/control', json={'action': 'emergency'}).status_code == 403
        assert client.post('/api/assistance/control', json={'action': 'emergency'},
                           headers={'X-Operator-Token': 'paired'}).json()['vehicle']['emergency']
        assert client.post('/api/assistance/requests', json=payload() | {'notes': 'private'}).status_code == 422
