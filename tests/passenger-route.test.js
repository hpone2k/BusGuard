import test from 'node:test';
import assert from 'node:assert/strict';
import {parseLocation, routePresentation, ArrivalTracker} from '../static/passenger/route.js';
import {requestAvailability, requestBody, parseSaved} from '../static/passenger/core.js';

const stops = ['a', 'b', 'c', 'd', 'e'].map((id, i) => ({id, name: `Bus stop ${id.toUpperCase()}`, x: i / 5, y: .5}));
const config = {stops, route: {simulated: true, stops}};
const at = stop_id => ({mode: 'at_stop', stop_id});
const route = (id = 'trip:1', stop = 'a') => ({simulated: true, phase: 'at_stop', current_stop_id: stop,
  next_stop_id: stop === 'a' ? 'b' : 'a', progress: 0, arrival_event: {id, stop_id: stop}});
const view = {connected: true, enabled: true, stationary: true, stopId: 'a', boardingOpen: true, available: 12};

test('demo location accepts only configured stops and never implies GPS or an unknown stop', () => {
  assert.deepEqual(parseLocation(JSON.stringify(at('a')), stops), at('a'));
  assert.deepEqual(parseLocation(at('unconfigured'), stops), {mode: 'away'});
  assert.deepEqual(parseLocation('{broken', stops), {mode: 'away'});
  assert.deepEqual(parseLocation({mode: 'onboard', stop_id: 'secret'}, stops), {mode: 'onboard'});
});

test('all requests require current stop and correct demo location; future alighting is rejected', () => {
  assert.equal(requestAvailability(view, 'boarding', 'a', false, at('a')).allowed, true);
  for (const location of [{mode: 'away'}, {mode: 'onboard'}, at('b')]) {
    assert.equal(requestAvailability(view, 'boarding', 'a', false, location).allowed, false);
  }
  assert.equal(requestAvailability(view, 'alighting', 'a', false, {mode: 'onboard'}).allowed, true);
  assert.equal(requestAvailability(view, 'alighting', 'b', false, {mode: 'onboard'}).allowed, false);
  assert.equal(requestAvailability(view, 'alighting', 'a', false, at('a')).allowed, false);
  for (const patch of [{stationary: false}, {boardingOpen: false}, {connected: false}]) {
    assert.equal(requestAvailability({...view, ...patch}, 'alighting', 'a', false, {mode: 'onboard'}).allowed, false);
  }
  assert.equal(requestAvailability({...view, connected: false}, 'boarding', 'a', true, {mode: 'away'}).allowed, true);
});

test('private retry retains its original location even if current demo location changes', () => {
  const identity = {request_token: 'a'.repeat(64), client_request_id: '123'};
  const location = at('a');
  const body = requestBody(identity, 'boarding', 'a', ['extra_time'], location);
  location.stop_id = 'b';
  assert.deepEqual(body.location, at('a'));
  assert.deepEqual(parseSaved(JSON.stringify({body})).body, body);
  assert.throws(() => requestBody(identity, 'boarding', 'a', ['extra_time']), /location/);
});

test('route marker uses only current shared progress and hides when stale or inconsistent', () => {
  const state = {route: {...route(), phase: 'travelling', progress: .5}};
  const live = routePresentation(config, state, at('b'));
  assert.equal(live.known, true);
  assert.equal(live.marker.x, (live.current.x + live.next.x) / 2);
  assert.match(live.title, /Bus stop B/);
  assert.match(live.locationLabel, /Bus stop B/);
  assert.equal(routePresentation(config, state, at('b'), false).marker, null);
  for (const progress of [-1, 2, NaN, '0.5']) {
    assert.equal(routePresentation(config, {route: {...state.route, progress}}, at('b')).marker, null);
  }
});

test('route is a compact evenly spaced line regardless of geographic coordinates', () => {
  const live = routePresentation(config, {route: route()}, at('a'));
  assert.deepEqual(live.stops.map(stop => [stop.x, stop.y]), [[44, 64], [152, 64], [260, 64], [368, 64], [476, 64]]);
  assert.equal(live.motion, 'still');
  assert.equal(live.wrapping, false);
  assert.equal(live.marker.x, 44);
  const single = routePresentation({stops: [stops[0]]}, null, at('a'));
  assert.equal(single.stops[0].x, 260);
  assert.deepEqual(routePresentation({stops: []}, null, at('a')).stops, []);
});

test('return leg never slides the bus backwards through intermediate stops', () => {
  const state = {route: {...route('end', 'e'), phase: 'travelling', next_stop_id: 'a', progress: .5}};
  const returning = routePresentation(config, state, at('a'));
  assert.equal(returning.known, true);
  assert.equal(returning.wrapping, true);
  assert.equal(returning.marker, null);
  assert.equal(returning.motion, 'travelling');
  assert.match(returning.title, /Returning to Bus stop A/);
  assert.match(returning.detail, /next loop begins at Bus stop A/);
  const approaching = routePresentation(config, {route: {...state.route, phase: 'approaching'}}, at('a'));
  assert.equal(approaching.marker, null);
  assert.match(approaching.title, /Arriving at Bus stop A/);
  const stale = routePresentation(config, state, at('a'), false);
  assert.equal(stale.wrapping, false);
  assert.equal(stale.motion, 'still');
  const arrived = routePresentation(config, {route: route('new-loop', 'a')}, at('a'));
  assert.equal(arrived.wrapping, false);
  assert.equal(arrived.marker.x, 44);
});

test('arrival alerts do not replay at initial load, refresh, repeated polls or a newly selected location', () => {
  const tracker = new ArrivalTracker();
  const options = {enabled: true, location: at('a')};
  assert.equal(tracker.observe(route('one'), options), null);
  assert.equal(tracker.observe(route('one'), options), null);
  assert.equal(tracker.observe(route('two', 'b'), options), null);
  assert.equal(tracker.observe(route('two', 'b'), {...options, location: at('b')}), null);
  assert.deepEqual(tracker.observe(route('three'), options), {id: 'three', stop_id: 'a'});
  const reloaded = new ArrivalTracker(tracker.lastId);
  assert.equal(reloaded.observe(route('three'), options), null);
});

test('only a fresh matching arrival alerts, including selected alighting stop for an onboard passenger', () => {
  const tracker = new ArrivalTracker();
  const options = {enabled: true, location: {mode: 'onboard'}, selectedStop: 'b'};
  tracker.observe(route('one'), options);
  assert.equal(tracker.observe(route('two', 'b'), {...options, fresh: false}), null);
  assert.deepEqual(tracker.observe(route('two', 'b'), options), {id: 'two', stop_id: 'b'});
  assert.equal(tracker.observe(route('three', 'b'), {...options, location: {mode: 'away'}}), null);
  assert.equal(tracker.observe({...route('four', 'b'), phase: 'approaching'}, options), null);
  assert.equal(tracker.observe(route('five', 'b'), {...options, enabled: false}), null);
});
