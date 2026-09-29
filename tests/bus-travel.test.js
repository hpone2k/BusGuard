import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import {ACCELERATION_MS, CRUISE_SPEED, createTravelMotion, roadOffset, travelPresentation, observationAge, shouldWakeTravel} from '../static/bus/src/travel-motion.js';
import {createRenderLoop} from '../static/bus/src/render-loop.js';
import {buildRoad} from '../static/bus/src/road.js';

function snapshot(phase = 'travelling', elapsed = 3000, remaining = 2000) {
  return {assistance: {simulation: true, vehicle: {motion: phase === 'braking' ? 'braking' : 'moving', doors: 'closed', ramp: 'stowed'},
    scenario: {enabled: true, cycle_id: 1, phase, braking_duration_ms: 2000, braking_remaining_ms: remaining,
      motion: {started_at_ms: 100000, elapsed_ms: elapsed, duration_ms: phase === 'braking' ? 2000 : 0, remaining_ms: remaining}}}};
}

test('travel visuals require fresh safe authoritative motion and never move with an open door', () => {
  assert.equal(travelPresentation(snapshot()).phase, 'moving');
  assert.equal(travelPresentation(snapshot(), 2000).phase, 'stationary');
  assert.equal(travelPresentation(snapshot(), 0, false).phase, 'stationary');
  for (const property of ['emergency', 'obstruction', 'revalidation_required']) {
    const state = snapshot(); state.assistance.vehicle[property] = true;
    assert.equal(travelPresentation(state).phase, 'stationary');
  }
  for (const [property, value] of [['doors', 'opening'], ['ramp', 'deployed']]) {
    const state = snapshot(); state.assistance.vehicle[property] = value;
    assert.equal(travelPresentation(state).phase, 'stationary');
  }
  const state = snapshot('braking'); state.assistance.scenario.braking_remaining_ms = NaN;
  assert.equal(travelPresentation(state).phase, 'stationary');
});

test('a response arriving after the rAF timestamp remains fresh and travel stays scheduled', () => {
  const pending = [], motion = createTravelMotion();
  const receivedAt = 103;
  let lastMotion;
  const loop = createRenderLoop(frameAt => {
    lastMotion = motion.step(travelPresentation(snapshot(), observationAge(frameAt, receivedAt)), .016);
    return lastMotion.active;
  }, {request(callback) { pending.push(callback); return pending.length; }, cancel() {}});
  loop.invalidate();
  pending.shift()(100); // The callback's frame began before the response arrived.
  assert.equal(lastMotion.speed, CRUISE_SPEED);
  assert.equal(pending.length, 1, 'The first fresh travelling frame must schedule its successor');
  const firstDistance = lastMotion.distance;
  pending.shift()(116);
  assert(lastMotion.distance > firstDistance);
  assert.equal(observationAge(NaN, receivedAt), Infinity);
  loop.dispose();
});

test('unchanged travelling state wakes an idle renderer without waking static or reduced motion views', () => {
  const moving = travelPresentation(snapshot());
  assert.equal(shouldWakeTravel(moving, moving.key, true), true);
  assert.equal(shouldWakeTravel(moving, moving.key, false), false);
  assert.equal(shouldWakeTravel(moving, moving.key, true, true), false);
  const idle = travelPresentation(null);
  assert.equal(shouldWakeTravel(idle, idle.key, true), false);
  const braking = travelPresentation(snapshot('braking', 0, 500));
  assert.equal(shouldWakeTravel(braking, braking.key, true), true);
  const brakeComplete = travelPresentation(snapshot('braking', 0, 0));
  assert.equal(shouldWakeTravel(brakeComplete, brakeComplete.key, true), false);
});

test('acceleration is smooth and braking reaches zero before door opening', () => {
  const motion = createTravelMotion();
  let previousSpeed = 0;
  for (let elapsed = 0; elapsed <= ACCELERATION_MS; elapsed += 100) {
    const result = motion.step(travelPresentation(snapshot('travelling', elapsed)), .016);
    assert(result.speed >= previousSpeed && result.speed <= CRUISE_SPEED); previousSpeed = result.speed;
  }
  assert.equal(previousSpeed, CRUISE_SPEED);
  for (let remaining = 2000; remaining >= 0; remaining -= 100) {
    const result = motion.step(travelPresentation(snapshot('braking', 0, remaining)), .016);
    assert(result.speed <= previousSpeed && result.speed >= 0); previousSpeed = result.speed;
  }
  assert.equal(previousSpeed, 0);
  const idle = motion.step(travelPresentation(null), .016);
  assert.equal(idle.active, false);
});

test('an early stop does not jump from partial acceleration to cruising speed', () => {
  const motion = createTravelMotion();
  const accelerating = motion.step(travelPresentation(snapshot('travelling', 600)), .016);
  const braking = motion.step(travelPresentation(snapshot('braking', 0, 1800)), .016);
  assert.equal(braking.speed, accelerating.speed);
  assert(braking.speed < CRUISE_SPEED / 2);
  const later = motion.step(travelPresentation(snapshot('braking', 0, 1800), 1800), .016);
  assert.equal(later.speed, 0);
});

test('late braking updates finish within the remaining server window and jitter never accelerates', () => {
  const motion = createTravelMotion();
  motion.step(travelPresentation(snapshot()), .016);
  const late = motion.step(travelPresentation(snapshot('braking', 0, 400)), .016);
  const halfway = motion.step(travelPresentation(snapshot('braking', 0, 400), 200), .016);
  assert(halfway.speed < late.speed);
  const jitter = motion.step(travelPresentation(snapshot('braking', 0, 250)), .016);
  assert.equal(jitter.speed, halfway.speed);
  const stopped = motion.step(travelPresentation(snapshot('braking', 0, 0)), .016);
  assert.equal(stopped.speed, 0); assert.equal(stopped.active, false);
  // If the entire braking interval was missed, an open-door snapshot freezes
  // immediately instead of inventing a local period of driving with open doors.
  const doors = snapshot('opening'); doors.assistance.vehicle.doors = 'opening';
  assert.equal(motion.step(travelPresentation(doors), .016).active, false);
});

test('offline and reduced motion freeze distance, while long suspended frames cannot jump the road', () => {
  const motion = createTravelMotion();
  const moving = motion.step(travelPresentation(snapshot()), .016);
  const offline = motion.step(travelPresentation(snapshot(), 0, false), 100);
  assert.equal(offline.distance, moving.distance);
  const reduced = motion.step(travelPresentation(snapshot()), .016, true);
  assert.equal(reduced.distance, moving.distance); assert.equal(reduced.active, false);
  const resumed = motion.step(travelPresentation(snapshot()), 100);
  assert(resumed.distance - moving.distance <= CRUISE_SPEED * .05);
});

test('road travels backwards with seamless bounded repetition and no moving shadow casters', () => {
  const road = buildRoad(new THREE.Scene());
  road.setTravel(7.25);
  const line = road.root.getObjectByName('High-contrast lane dashes');
  assert.equal(line.position.x, -1.25);
  assert.equal(roadOffset(6), 0);
  assert.equal(roadOffset(6 * 1e8 + 1.25), -1.25);
  road.root.traverse(object => assert.equal(object.castShadow, false));
  assert(road.root.children.length <= 30, 'Road and passing landmarks must stay within a bounded draw-call budget');
  road.setTravel(NaN); assert.equal(line.position.x, 0);
});

test('road landmarks and grain repeat together without new geometry or instance uploads', () => {
  const road = buildRoad(new THREE.Scene());
  const instances = road.root.children.filter(object => object.isInstancedMesh);
  const versions = instances.map(object => object.instanceMatrix.version);
  const asphalt = road.root.getObjectByName('Textured charcoal asphalt');
  const texture = asphalt.material.map, textureVersion = texture.version;
  road.setTravel(7.25);
  const positions = instances.map(object => object.position.x), grainOffset = texture.offset.x;
  road.setTravel(79.25);
  assert.deepEqual(instances.map(object => object.position.x), positions);
  assert.equal(texture.offset.x, grainOffset);
  assert.deepEqual(instances.map(object => object.instanceMatrix.version), versions);
  assert.equal(texture.version, textureVersion);
  assert.equal(texture.image.width, 128);
  assert.equal(texture.image.height, 128);
});
