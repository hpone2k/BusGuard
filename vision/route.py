"""Server-owned schematic route for the local demonstration; never GPS data."""
import copy


STOPS = [
    dict(id='campus', name='Bus stop A', x=.13, y=.67),
    dict(id='interchange', name='Bus stop B', x=.29, y=.27),
    dict(id='community', name='Bus stop C', x=.59, y=.20),
    dict(id='stop_d', name='Bus stop D', x=.87, y=.48),
    dict(id='stop_e', name='Bus stop E', x=.66, y=.81),
]
STOP_IDS = tuple(stop['id'] for stop in STOPS)


def next_stop(stop_id):
    return STOP_IDS[(STOP_IDS.index(stop_id) + 1) % len(STOP_IDS)]


def stop_name(stop_id):
    return next(stop['name'] for stop in STOPS if stop['id'] == stop_id)


def config():
    return dict(id='demo-loop', simulated=True, loop=True, stops=copy.deepcopy(STOPS))


def snapshot(owner):
    scenario, vehicle, now = owner.scenario, owner.vehicle, owner.clock()
    current = vehicle['stop_id']
    target = next_stop(current)
    phase, progress = 'at_stop', 0.
    if vehicle['motion'] == 'moving':
        phase = 'travelling'
        elapsed = max(0., now - (scenario.motion_started_at or now))
        progress = min(.85, elapsed / 30. * .85)
    elif vehicle['motion'] == 'braking':
        phase = 'approaching'
        current, target = scenario.route_from_stop or current, current
        elapsed = max(0., now - (scenario.motion_started_at or now))
        start = scenario.route_approach_progress
        progress = start + (1 - start) * min(1., elapsed / scenario.BRAKE_SECONDS)
    return dict(current_stop_id=current, next_stop_id=target, phase=phase,
                progress=round(progress, 5), arrival_event=copy.deepcopy(scenario.arrival_event),
                simulated=True)
