import pytest

from vision.cabin_count import CabinCount


def source(count, frame, now, **changes):
    result = dict(kind='live', connected=True, is_demo=False, prompt_mode='objects',
                  session_id='inside-1', frame_id=frame, age_ms=0, received_at_ms=now * 1000,
                  count_summary=dict(age_ms=0, coverage=1, classes={
                      'person': dict(stable=count, support=.8, status='stable')}))
    result.update(changes)
    return result


def observe(gate, count, start=100, frames=4, *, doors=True, stationary=True, **changes):
    changed = False
    for i in range(frames):
        now = start + i * .3
        changed |= gate.observe(source(count, round(now * 10), now, **changes), now,
                                stationary=stationary, doors_open=doors)
    return changed


def test_stable_visible_people_change_capacity_only_after_new_frames():
    gate = CabinCount()
    packet = source(7, 1, 100)
    gate.observe(packet, 100, stationary=True, doors_open=True)
    for _ in range(20):
        assert not gate.observe(packet, 100.8, stationary=True, doors_open=True)
    assert gate.count is None
    assert observe(gate, 7, 101)
    assert gate.count == 7


@pytest.mark.parametrize('changes', [dict(kind='image'), dict(kind='video'), dict(is_demo=True),
    dict(connected=False), dict(age_ms=1000), dict(prompt_mode='phrase'),
    dict(count_summary={'age_ms': 0, 'coverage': 1, 'classes': {'person': {'status': 'uncertain', 'stable': 26, 'support': .5}}}),
    dict(count_summary={'age_ms': 0, 'coverage': 1, 'classes': {'a person with a red shirt': {'status': 'stable', 'stable': 26, 'support': 1}}}),
    dict(count_summary={'age_ms': 0, 'coverage': 1, 'classes': {'person': {'status': 'stable', 'stable': True, 'support': 1}}}),
])
def test_only_fresh_literal_person_votes_from_live_objects_source_count(changes):
    gate = CabinCount()
    assert not observe(gate, 26, **changes)
    assert gate.count is None


def test_partial_view_never_frees_capacity_from_disappearing_person():
    gate = CabinCount()
    observe(gate, 7)
    assert not observe(gate, 5, 102, frames=15)
    assert gate.count == 7


def test_whole_cabin_decrease_needs_two_point_five_seconds_open_doors():
    gate = CabinCount()
    gate.configure(True)
    observe(gate, 7)
    assert not observe(gate, 5, 102, frames=9)
    assert gate.count == 7
    assert observe(gate, 5, 104.7, frames=1)
    assert gate.count == 5
    assert not observe(gate, 0, 106, frames=15, doors=False)
    assert gate.count == 5


def test_gap_source_change_and_reconnect_do_not_accumulate_evidence():
    gate = CabinCount()
    gate.configure(True)
    observe(gate, 7)
    observe(gate, 5, 102, frames=8)
    observe(gate, 5, 106, frames=1)
    assert gate.count == 7
    observe(gate, 5, 106.3, frames=8, session_id='replacement')
    assert gate.count == 7


def test_accepted_passenger_event_protects_capacity_from_delayed_camera():
    gate = CabinCount()
    gate.configure(True)
    observe(gate, 25)
    gate.event(25, 1, 101)
    assert gate.count == 26
    observe(gate, 25, 101.1, frames=16)
    assert gate.count == 26
    assert observe(gate, 25, 106, frames=10)
    assert gate.count == 25


def test_retained_count_survives_stale_source_and_restart_but_not_as_fresh():
    gate = CabinCount()
    observe(gate, 7)
    assert gate.snapshot(103)['status'] == 'retained'
    restored = CabinCount()
    restored.restore(gate.dump())
    assert restored.count == 7
    assert restored.snapshot(103)['status'] == 'retained'


def test_moving_source_updates_count_without_releasing_seats_on_occlusion():
    gate = CabinCount()
    observe(gate, 7)
    assert observe(gate, 26, 102, stationary=False, doors=False)
    assert gate.count == 26
    gate.configure(True)
    assert not observe(gate, 20, 104, frames=12, stationary=False, doors=False)
    assert gate.count == 26
    # Even an inconsistent moving/open-door input cannot prove an exit.
    assert not observe(gate, 20, 108, frames=12, stationary=False, doors=True)
    assert gate.count == 26


@pytest.mark.parametrize('label', ['person', 'persons', ' PERSON ', 'Persons'])
def test_literal_person_alias_is_counted_during_travel(label):
    gate = CabinCount()
    summary = dict(age_ms=0, coverage=1, classes={
        label: dict(stable=7, support=.8, status='stable')})
    assert observe(gate, 7, stationary=False, doors=False, count_summary=summary)
    assert gate.count == 7


def test_overlapping_person_aliases_cannot_be_arbitrarily_selected_or_summed():
    gate = CabinCount()
    summary = dict(age_ms=0, coverage=1, classes={
        'person': dict(stable=7, support=.8, status='stable'),
        'persons': dict(stable=3, support=.8, status='stable')})
    assert not observe(gate, 7, count_summary=summary)
    assert gate.count is None


def test_travel_transition_keeps_count_evidence_but_closure_cancels_a_decrease():
    gate = CabinCount()
    assert not observe(gate, 7, frames=2)
    assert observe(gate, 7, 100.6, frames=1, stationary=False, doors=False)
    assert gate.count == 7
    gate.configure(True)
    assert not observe(gate, 5, 102, frames=7)
    assert not observe(gate, 5, 104.1, frames=1, stationary=False, doors=False)
    assert not observe(gate, 5, 104.4, frames=8)
    assert gate.count == 7
    assert observe(gate, 5, 106.8, frames=2)
    assert gate.count == 5
