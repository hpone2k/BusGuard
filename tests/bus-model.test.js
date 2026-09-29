import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import {buildBus, CAMERA_LAYOUT, makeCctvCamera} from '../static/bus/src/model.js';

test('exterior camera sits halfway between both boarding doors and looks outwards', () => {
  const {position, target} = CAMERA_LAYOUT.outside;
  const frontDoor = 4.75, middleDoor = .70;
  assert.equal(position[0], (frontDoor + middleDoor) / 2);
  assert(position[1] > 2.5, 'Camera should be above approaching passengers');
  assert(position[2] > 1.32, 'Camera must sit outside the bodywork');
  assert(target[2] > position[2], 'Camera must face the kerb, not the cabin');
  assert(target[1] < position[1], 'Camera should angle down toward the boarding area');
});

test('actual exterior projection includes both boarding approaches at seated and standing heights', () => {
  const camera = makeCctvCamera(CAMERA_LAYOUT.outside);
  const frustum = new THREE.Frustum().setFromProjectionMatrix(
    new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
  for (const doorX of [.70, 4.75]) {
    for (const height of [.70, 1.2, 1.8]) {
      // Approach corridor outside the body, not a claim of zero near-wall blind spots.
      const approach = new THREE.Vector3(doorX, height, 2.4);
      const projected = approach.clone().project(camera);
      assert(frustum.containsPoint(approach), `Door ${doorX}, height ${height} is outside the lens`);
      assert(Math.abs(projected.x) < 1 && Math.abs(projected.y) < 1);
      assert(projected.z > -1 && projected.z < 1);
    }
  }
  assert(!frustum.containsPoint(new THREE.Vector3(2.725, 1.2, -2)),
    'A point behind the bus must not be treated as exterior coverage');
});

test('CCTV optical origin clears its own housing and centres the intended target', () => {
  for (const spec of Object.values(CAMERA_LAYOUT)) {
    const camera = makeCctvCamera(spec, 16 / 9);
    const mount = new THREE.Vector3(...spec.position), target = new THREE.Vector3(...spec.target);
    const forward = target.clone().sub(mount).normalize();
    assert(camera.position.clone().sub(mount).dot(forward) > .22,
      'The virtual lens must start beyond its physical casing');
    const aim = target.clone().project(camera);
    assert(Math.abs(aim.x) < 1e-12 && Math.abs(aim.y) < 1e-12);
    assert.equal(camera.aspect, 16 / 9);
    assert(camera.getWorldDirection(new THREE.Vector3()).distanceTo(forward) < 1e-12);
  }
});

test('model coverage matches the rendered lens and glazing leaves the cabin visible', () => {
  // Build real Three.js geometry without a GPU. Canvas drawing is irrelevant to
  // ray intersections and camera calibration, so only that browser surface is stubbed.
  const savedDocument = globalThis.document;
  globalThis.document = {createElement(name) {
    assert.equal(name, 'canvas');
    const ctx = Object.fromEntries(['fillRect', 'clearRect', 'beginPath', 'roundRect', 'fill',
      'stroke', 'arc', 'moveTo', 'lineTo', 'fillText'].map(method => [method, () => {}]));
    return {width: 1, height: 1, getContext: () => ctx};
  }};
  let bus;
  try { bus = buildBus(new THREE.Scene()); }
  finally {
    if (savedDocument === undefined) delete globalThis.document;
    else globalThis.document = savedDocument;
  }
  bus.chassis.updateMatrixWorld(true);
  const volumes = bus.coverage.children.filter(object => object.isMesh);
  assert.equal(volumes.length, 2);
  for (const [index, name] of ['inside', 'outside'].entries()) {
    const camera = bus.cameras[name].camera, vertices = volumes[index].geometry.attributes.position;
    let cornerCount = 0;
    for (let i = 0; i < vertices.count; i++) {
      const point = new THREE.Vector3().fromBufferAttribute(vertices, i);
      if (point.distanceTo(camera.position) < 1e-5) continue;
      const projected = point.project(camera);
      assert(Math.abs(Math.abs(projected.x) - 1) < 1e-5, `${name}: coverage width differs from lens`);
      assert(Math.abs(Math.abs(projected.y) - 1) < 1e-5, `${name}: coverage height differs from lens`);
      cornerCount++;
    }
    assert(cornerCount > 0);
  }

  const shell = bus.chassis.getObjectByName('Exterior body and glazing');
  const ray = new THREE.Raycaster(new THREE.Vector3(-4.85, 2.1, 3), new THREE.Vector3(0, 0, -1));
  const nearWindow = ray.intersectObject(shell, true).filter(hit => hit.point.z > 1.2);
  assert(nearWindow.length > 0, 'A glazed pane should remain in the outer shell');
  assert(nearWindow.every(hit => hit.object.material.transparent && hit.object.material.opacity < .5),
    'Opaque backing behind the glazing would hide passengers and the interior');

  const roof = bus.chassis.getObjectByName('Liftaway roof');
  bus.setCutaway(1);
  assert.equal(roof.visible, false, 'HVAC, hatch and all roof details must disappear together in the cabin view');
  assert.equal(shell.visible, false, 'Exterior panels must not obstruct the cabin cutaway');
  assert(bus.seats.every(seat => seat.group.visible), 'Cutaway must preserve all 26 passenger seats');
  bus.setCutaway(0);
  assert.equal(roof.visible, true);
  assert.equal(shell.visible, true);
  assert.equal(roof.position.y, 0, 'Returning outside should restore the roof to its original height');
});
