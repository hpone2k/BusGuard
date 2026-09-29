import pytest

from vision.assistance import AssistanceController
from vision.cabin_audit import CabinAuditResult


def result(rounded, completed_at=100):
    return CabinAuditResult(1, float(rounded), rounded, completed_at - 30, completed_at, 'inside')


def test_scan_after_camera_window_is_not_erased_by_late_result():
    now = [100.0]
    c = AssistanceController(clock=lambda: now[0])
    c.scenario_control('start')
    now[0] += .8
    c.tick()
    now[0] = 101.2
    c.scenario_rfid('new-card', 'person', simulated=True)
    c.scenario.reconcile_camera(result(4, completed_at=101.0))
    assert c.scenario.occupancy_total() == 5
    assert c.scenario.seats()['confirmed_total'] == 1
    assert c.scenario.camera_check()['mismatch'] == 4


def test_reconciled_raw_ledger_can_exceed_original_seat_group_without_invalid_restart(tmp_path):
    now = [100.0]
    c = AssistanceController(tmp_path / 'state.json', clock=lambda: now[0])
    c.scenario_control('load', priority_occupied=0, standard_occupied=20)
    c.scenario.reconcile_camera(result(5))
    c.scenario_control('start')
    now[0] += .8
    c.tick()
    for index in range(5):
        c.scenario_rfid(f'card-{index}', 'person', simulated=True)
    state = c.snapshot()
    assert state['scenario']['seats']['confirmed_total'] == 25
    assert state['scenario']['seats']['total'] == 10
    assert state['scenario']['seats']['standard']['occupied'] == 10
    restored = AssistanceController(c.path, clock=lambda: now[0])
    assert not restored.vehicle['emergency']
    assert restored.scenario.occupancy_total() == 10


def test_old_instantaneous_camera_count_never_becomes_new_audit_result(tmp_path):
    now = [100.0]
    c = AssistanceController(tmp_path / 'state.json', clock=lambda: now[0])
    c.cabin.count = 4
    c._save()
    restored = AssistanceController(c.path, clock=lambda: now[0])
    seats = restored.scenario.seats()
    assert seats['confirmed_total'] == 0
    assert seats['total'] == 0
    assert seats['available'] == 26
    assert seats['camera_check']['last_mean'] is None


@pytest.mark.parametrize('boundary,tap_at,expected_total,expected_recorded', [
    (101.0004, 101.00035, 4, 1),
    (101.0004, 101.00045, 5, 0),
    (101.0006, 101.00055, 4, 1),
    (101.0006, 101.00065, 5, 0),
])
def test_fractional_boundary_uses_exact_tap_order_not_rounded_display_milliseconds(
        boundary, tap_at, expected_total, expected_recorded):
    now = [100.0]
    c = AssistanceController(clock=lambda: now[0])
    c.scenario_control('start')
    now[0] = 100.8
    c.tick()
    now[0] = tap_at
    c.scenario_rfid('near-boundary-card', 'person', simulated=True)
    # Result processing can follow the window boundary and the tap. The tap
    # belongs in the correction only if its precise admission was after it.
    now[0] = max(boundary, tap_at) + .1
    c.scenario.reconcile_camera(result(4, completed_at=boundary))
    assert c.scenario.occupancy_total() == expected_total
    assert c.scenario.seats()['confirmed_total'] == 1
    assert c.scenario.camera_reconciliation['recorded_at_check'] == expected_recorded
    assert c.scenario.camera_check()['mismatch'] == 4 - expected_recorded


def test_late_camera_only_alighting_preserves_raw_ledger_for_mismatch():
    now = [100.0]
    c = AssistanceController(clock=lambda: now[0])
    # Five riders were observed by camera, with no recorded boarding taps.
    c.scenario.reconcile_camera(result(5, completed_at=100.0))
    c.scenario_control('start')
    now[0] = 100.8
    c.tick()
    now[0] = 101.2
    state = c.scenario_control('alight', seat_type='standard', event_id='camera-only-exit')
    assert state['scenario']['last_admission']['allowed']
    assert state['scenario']['seats']['total'] == 4
    assert state['scenario']['seats']['confirmed_total'] == 0
    c.scenario.reconcile_camera(result(5, completed_at=101.0))
    seats = c.scenario.seats()
    assert seats['total'] == 4
    assert seats['confirmed_total'] == 0
    assert seats['available'] == 22
    assert c.scenario.camera_reconciliation['recorded_at_check'] == 0
    assert c.scenario.camera_check()['mismatch'] == 5
