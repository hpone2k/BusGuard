"""Explicit posture stub for tests of unrelated timing/RFID contracts.

Actual fresh-frame, standing and confirmation-duration behavior is exercised in
test_standing_departure, test_continuous_posture and test_quiet_empty_stop.
"""


def assume_clear_standing_check(controller):
    controller.posture_enabled = True
    controller.departure.result = lambda _: dict(
        can_depart=True, status='ready', message='Standing check cleared in timing test.',
        mode='standing_only', progress_ms=3000, confirmation_ms=3000,
        expected_people=controller.scenario.occupancy_total())


def use_real_standing_check(controller):
    controller.posture_enabled = True
    controller.departure.result = type(controller.departure).result.__get__(controller.departure)
