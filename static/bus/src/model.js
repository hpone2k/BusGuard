import * as THREE from 'three';
import {RoundedBoxGeometry} from 'three/addons/geometries/RoundedBoxGeometry.js';
import {mergeGeometries} from 'three/addons/utils/BufferGeometryUtils.js';
import {SEATS, clamp, smooth} from './simulation.js';

const V = (x, y, z) => new THREE.Vector3(x, y, z);
export const CAMERA_LAYOUT = Object.freeze({
  inside: Object.freeze({position: Object.freeze([5.02, 2.70, -.16]), target: Object.freeze([-3.2, .25, .07]), fov: 84}),
  // The lens sits on the body halfway between the front (4.75 m) and middle
  // (.70 m) doorway centres. A wide field covers both exterior approaches.
  outside: Object.freeze({position: Object.freeze([2.725, 2.72, 1.45]), target: Object.freeze([2.725, .85, 3.6]), fov: 92})
});
export const ENTRANCE_LED_LAYOUT = Object.freeze({
  // Kerb-side front entrance. The panel sits above the moving door leaves,
  // below the roof cap, with its lit face looking out towards passengers.
  position: Object.freeze([4.75, 2.88, 1.366]),
  size: Object.freeze([1.49, .245, .075]),
});
export function makeCctvCamera(spec, aspect = 480 / 260) {
  const position = V(...spec.position), target = V(...spec.target);
  const camera = new THREE.PerspectiveCamera(spec.fov, aspect, .06, 55);
  camera.position.copy(position); camera.lookAt(target);
  // Optical origin is beyond the housing, so the rendered feed cannot see its casing.
  camera.position.add(target.clone().sub(position).normalize().multiplyScalar(.25));
  camera.updateMatrixWorld(); return camera;
}
const geometries = new Map();
const materials = {};
function material(name, color, options = {}) {
  return materials[name] ||= new THREE.MeshStandardMaterial({color, roughness: .48, metalness: .08, ...options});
}
function rounded(w, h, d, radius = .035) {
  const r = Math.min(radius, w / 2, h / 2, d / 2), key = `${w}/${h}/${d}/${r}`;
  if (!geometries.has(key)) geometries.set(key, new RoundedBoxGeometry(w, h, d, 3, r));
  return geometries.get(key);
}
function add(parent, geometry, mat, x = 0, y = 0, z = 0) {
  const mesh = new THREE.Mesh(geometry, mat); mesh.position.set(x, y, z);
  mesh.castShadow = true; mesh.receiveShadow = true; parent.add(mesh); return mesh;
}
function box(parent, size, position, mat, radius = .035) { return add(parent, rounded(...size, radius), mat, ...position); }
function rod(parent, a, b, radius, mat) {
  const start = V(...a), end = V(...b), delta = end.clone().sub(start);
  const mesh = add(parent, new THREE.CylinderGeometry(radius, radius, delta.length(), 10), mat);
  mesh.position.copy(start.add(end).multiplyScalar(.5)); mesh.quaternion.setFromUnitVectors(V(0, 1, 0), delta.normalize()); return mesh;
}
function ring(parent, radius, tube, position, mat, rotation = [0, 0, 0], arc = Math.PI * 2) {
  const mesh = add(parent, new THREE.TorusGeometry(radius, tube, 8, 48, arc), mat, ...position); mesh.rotation.set(...rotation); return mesh;
}
function texture(draw, w = 1024, h = 256) {
  const canvas = document.createElement('canvas'); canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext('2d'); draw(ctx, w, h);
  const map = new THREE.CanvasTexture(canvas); map.colorSpace = THREE.SRGBColorSpace; map.anisotropy = 8; return map;
}
function sign(parent, text, width, height, position, rotation = [0, 0, 0], options = {}) {
  const map = texture((ctx, w, h) => {
    if (options.bg !== false) { ctx.fillStyle = options.bg || '#111d22'; ctx.fillRect(0, 0, w, h); }
    ctx.fillStyle = options.color || '#e9f4e4'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.font = `${options.weight || 600} ${h * (options.fontSize || .58)}px "Segoe UI", sans-serif`;
    ctx.fillText(text, w / 2, h * .52, w * .93);
    if (options.led) {
      ctx.globalCompositeOperation = 'destination-out';
      for (let x = 0; x < w; x += 4) ctx.clearRect(x, 0, 1, h);
      for (let y = 0; y < h; y += 4) ctx.clearRect(0, y, w, 1);
    }
  }, 1024, 128);
  // A flat decal has no front/back volume to sort: one double-sided pass suffices.
  const mesh = add(parent, new THREE.PlaneGeometry(width, height), new THREE.MeshBasicMaterial({map, transparent: true, side: THREE.DoubleSide, forceSinglePass: true}), ...position);
  mesh.rotation.set(...rotation); mesh.castShadow = false; return mesh;
}

function entranceDisplay(parent) {
  const group = new THREE.Group(); group.name = 'Entrance seat availability LED';
  group.position.set(...ENTRANCE_LED_LAYOUT.position); parent.add(group);
  const [width, height, depth] = ENTRANCE_LED_LAYOUT.size;
  box(group, [width, height, depth], [0, 0, 0], material('ledBezel', '#26352e', {metalness: .72, roughness: .26}), .019);
  box(group, [width - .024, height - .024, .012], [0, 0, depth / 2], material('ledGasket', '#030b07', {roughness: .76}), .012);
  const canvas = document.createElement('canvas'); canvas.width = 1152; canvas.height = 168;
  const context = canvas.getContext('2d');
  const map = new THREE.CanvasTexture(canvas); map.colorSpace = THREE.SRGBColorSpace; map.anisotropy = 8;
  const face = add(group, new THREE.PlaneGeometry(width - .057, height - .057),
    new THREE.MeshBasicMaterial({map, toneMapped: false}), 0, 0, depth / 2 + .008);
  face.name = 'Live entrance LED face'; face.castShadow = false; face.receiveShadow = false;
  // A thin, clear cover catches a real surface highlight without dimming the
  // unlit display. One canvas texture replaces thousands of individual bulbs.
  const cover = add(group, new THREE.PlaneGeometry(width - .038, height - .038),
    new THREE.MeshPhysicalMaterial({color: '#d8eee0', transparent: true, opacity: .065,
      metalness: .15, roughness: .13, clearcoat: 1, depthWrite: false}), 0, 0, depth / 2 + .010);
  cover.castShadow = false; cover.receiveShadow = false;
  let lastKey = '';
  function update(view) {
    const key = JSON.stringify([view.seatsLine, view.statusLine, view.tone]);
    if (lastKey === key) return false;
    lastKey = key;
    context.fillStyle = '#041009'; context.fillRect(0, 0, canvas.width, canvas.height);
    context.textAlign = 'center'; context.textBaseline = 'middle';
    context.fillStyle = view.tone === 'muted' ? '#a1baa4' : '#c9f696';
    context.font = '600 57px "Consolas", "Courier New", monospace';
    context.fillText(view.seatsLine, canvas.width / 2, 41, canvas.width - 34);
    context.fillStyle = view.tone === 'warning' ? '#ffc76b' : view.tone === 'muted' ? '#a1baa4' : '#e3ffb4';
    context.font = '700 65px "Consolas", "Courier New", monospace';
    context.fillText(view.statusLine, canvas.width / 2, 121, canvas.width - 34);
    // Dark gaps over the glyphs give a stable physical dot-matrix pattern;
    // there is no blinking, scrolling, or continuously animated texture.
    context.fillStyle = '#041009';
    for (let x = 0; x < canvas.width; x += 4) context.fillRect(x, 0, 1, canvas.height);
    for (let y = 0; y < canvas.height; y += 4) context.fillRect(0, y, canvas.width, 1);
    map.needsUpdate = true;
    group.userData.display = {...view};
    return true;
  }
  update({seatsLine: 'PRIORITY --   STANDARD --', statusLine: 'CONNECTING', tone: 'muted'});
  return {group, face, update};
}
function accessibleSign(parent, size, position, rotation = [0, 0, 0], floor = false) {
  const map = texture((c, w, h) => {
    c.fillStyle = floor ? '#457168' : '#25618b'; c.beginPath(); c.roundRect(0, 0, w, h, 28); c.fill();
    c.strokeStyle = '#f3f6e5'; c.lineWidth = 13; c.lineCap = 'round'; c.lineJoin = 'round';
    c.beginPath(); c.arc(118, 53, 18, 0, Math.PI * 2); c.stroke();
    c.beginPath(); c.moveTo(112, 86); c.lineTo(121, 150); c.lineTo(177, 150); c.lineTo(205, 201); c.lineTo(224, 191); c.moveTo(117, 112); c.lineTo(172, 112); c.stroke();
    c.beginPath(); c.arc(100, 167, 49, -.25 * Math.PI, 1.47 * Math.PI); c.stroke();
  }, 256, 256);
  const mesh = add(parent, new THREE.PlaneGeometry(size, size), new THREE.MeshBasicMaterial({map, side: THREE.DoubleSide, transparent: true, forceSinglePass: true}), ...position);
  mesh.rotation.set(...rotation); mesh.castShadow = false; return mesh;
}

// Open frames around a single laminated pane: opaque backing would hide the cabin
// and obstruct the interior camera even though the outer surface looks like glass.
function glazing(parent, width, height, position, rotation, frame, glass, border = .045) {
  const group = new THREE.Group(); group.position.set(...position); group.rotation.set(...rotation); parent.add(group);
  for (const y of [-1, 1]) box(group, [width, border, .067], [0, y * (height - border) / 2, 0], frame, .014);
  for (const x of [-1, 1]) box(group, [border, height - border, .067], [x * (width - border) / 2, 0, 0], frame, .014);
  const pane = add(group, new THREE.PlaneGeometry(width - border * 1.45, height - border * 1.45), glass, 0, 0, .016);
  pane.castShadow = false;
  return group;
}

// Consolidate fixed parts by material, keeping doors, roof, seats and people independent.
function consolidate(group) {
  group.updateMatrixWorld(true);
  const inverse = group.matrixWorld.clone().invert(), buckets = new Map(), meshes = [];
  group.traverse(object => { if (object.isMesh && !Array.isArray(object.material)) meshes.push(object); });
  for (const mesh of meshes) {
    const key = mesh.material.uuid + (mesh.castShadow ? '1' : '0');
    if (!buckets.has(key)) buckets.set(key, {material: mesh.material, casts: mesh.castShadow, geometries: []});
    let geometry = mesh.geometry.clone().applyMatrix4(inverse.clone().multiply(mesh.matrixWorld));
    if (geometry.index) geometry = geometry.toNonIndexed();
    // Every geometry has a compatible position/normal/uv contract.
    if (!geometry.attributes.uv) geometry.setAttribute('uv', new THREE.Float32BufferAttribute(new Float32Array(geometry.attributes.position.count * 2), 2));
    buckets.get(key).geometries.push(geometry);
    mesh.removeFromParent();
  }
  for (const entry of buckets.values()) {
    const merged = mergeGeometries(entry.geometries);
    if (merged) { const mesh = add(group, merged, entry.material); mesh.castShadow = entry.casts; }
    entry.geometries.forEach(g => g.dispose());
  }
}

export function buildBus(scene) {
  const paint = new THREE.MeshPhysicalMaterial({color: '#80bb35', metalness: .28, roughness: .27, clearcoat: 1, clearcoatRoughness: .17});
  const black = material('black', '#152127', {roughness: .3, metalness: .3});
  const rubber = material('rubber', '#141b20', {roughness: .91, metalness: 0});
  const metal = material('metal', '#b1bec0', {roughness: .24, metalness: .83});
  const cream = material('cream', '#c8d4cf', {roughness: .67});
  const floorMap = texture((ctx, w, h) => {
    ctx.fillStyle = '#d0d5d1'; ctx.fillRect(0, 0, w, h);
    // Deterministic speckled, non-slip transit flooring rather than flat plastic.
    for (let i = 0; i < 7200; i++) {
      const x = (i * 79 + Math.floor(i / 71) * 17) % w, y = (i * 113 + Math.floor(i / 47) * 31) % h;
      ctx.fillStyle = ['#bdc4bf', '#e5e8e4', '#aeb9b3'][i % 3]; ctx.fillRect(x, y, i % 3 + 1, i % 2 + 1);
    }
  }, 512, 512); floorMap.wrapS = floorMap.wrapT = THREE.RepeatWrapping; floorMap.repeat.set(4, 2);
  const floor = material('floor', '#657772', {roughness: .97, metalness: 0, map: floorMap});
  const yellow = material('rails', '#edb849', {roughness: .3, metalness: .42});
  const white = material('roof', '#c4d0d0', {roughness: .32, metalness: .4});
  const glass = new THREE.MeshPhysicalMaterial({color: '#46797b', roughness: .10, metalness: .16, transparent: true, opacity: .29, clearcoat: 1, clearcoatRoughness: .10, envMapIntensity: 1.1, side: THREE.DoubleSide, depthWrite: false});
  const light = material('headlight', '#efffeb', {emissive: '#d4ffc0', emissiveIntensity: 2.6, roughness: .14});
  const amber = material('amberlight', '#f4ac49', {emissive: '#e99524', emissiveIntensity: .8});
  const red = material('taillight', '#b52223', {emissive: '#e72415', emissiveIntensity: 1.3});
  const chassis = new THREE.Group(); chassis.name = 'SG-026 autonomous bus'; scene.add(chassis);
  const fixed = new THREE.Group(), shell = new THREE.Group(), roof = new THREE.Group(), seatsGroup = new THREE.Group();
  fixed.name = 'Passenger cabin structure'; shell.name = 'Exterior body and glazing'; roof.name = 'Liftaway roof';
  chassis.add(fixed, shell, roof, seatsGroup);

  // Low floor, raised rear deck and a continuous centre aisle. No driving compartment.
  box(fixed, [11.62, .18, 2.44], [0, .46, 0], black, .09);
  // Exposed suspension and battery undertray make the low-angle silhouette credible.
  for (const z of [-.69, .69]) box(fixed, [9.7, .12, .11], [-.15, .29, z], black, .026);
  box(fixed, [4.00, .12, 1.33], [-.4, .31, 0], metal, .04);
  for (const x of [-3.86, 3.23]) {
    rod(fixed, [x, .48, -1.1], [x, .48, 1.1], .063, black);
    for (const z of [-.83, .83]) {
      add(fixed, new THREE.CylinderGeometry(.13, .13, .18, 16), rubber, x, .49, z);
      rod(fixed, [x - .32, .35, z], [x + .18, .59, z], .027, metal);
    }
  }
  box(fixed, [11.5, .075, 2.34], [0, .57, 0], floor, .02);
  box(fixed, [3.08, .30, 2.31], [-4.04, .72, 0], floor);
  box(fixed, [.26, .15, .57], [-2.40, .66, 0], floor);
  for (const [x,y] of [[-2.27,.74],[-2.51,.875]]) box(fixed, [.025, .015, .57], [x, y, 0], yellow, .005);
  box(fixed, [11.4, .015, .055], [0, .614, .31], yellow, .006);
  box(fixed, [11.4, .015, .055], [0, .614, -.31], yellow, .006);
  box(fixed, [2.15, .012, 1.0], [2.73, .616, .69], material('bay', '#3f706a', {roughness: .9}), .06);
  accessibleSign(fixed, .61, [2.55, .626, .7], [-Math.PI / 2, 0, Math.PI / 2], true);
  sign(fixed, 'MULTI-PURPOSE BAY', 1.8, .17, [2.7, .629, .21], [-Math.PI / 2, 0, 0], {bg: '#3f706a', color: '#d4ebe0'});
  box(fixed, [1.08, .72, .08], [1.9, 1.22, 1.17], cream);
  box(fixed, [1.00, .52, .06], [1.9, 1.2, 1.12], black);
  rod(fixed, [1.30, .65, 1.14], [1.30, 1.7, 1.14], .027, yellow);
  rod(fixed, [1.30, 1.7, 1.14], [2.50, 1.7, 1.14], .027, yellow);
  // Bay restraints and a reachable request button remain visible in the cutaway.
  for (const x of [1.55, 2.22]) {
    box(fixed, [.10, .12, .055], [x, 1.27, 1.06], black, .018);
    rod(fixed, [x, 1.24, 1.034], [x + .13, 1.06, 1.034], .017, rubber);
    box(fixed, [.07, .07, .035], [x + .13, 1.045, 1.032], metal, .012);
  }
  box(fixed, [.11, .16, .055], [1.30, 1.42, 1.07], black, .018);
  const requestButton = add(fixed, new THREE.CylinderGeometry(.038, .038, .017, 24), material('requestButton', '#477db8', {roughness: .3}), 1.30, 1.44, 1.032); requestButton.rotation.x = Math.PI / 2;
  sign(fixed, 'ASSIST', .086, .026, [1.30, 1.375, 1.035], [0, Math.PI, 0], {bg: false, color: '#e9f0e8'});
  for (const z of [-1.185, 1.185]) {
    const segments = z < 0 ? [[-5.40, 5.23]] : [[-5.40, -.12], [1.53, 4.04]];
    for (const [a, b] of segments) {
      box(fixed, [b - a, .15, .06], [(a + b) / 2, 1.44, z], cream, .02);
      box(fixed, [b - a, .025, .08], [(a + b) / 2, 1.53, z], metal, .008);
    }
  }

  // Lower side panels are cut around the tyres, rather than covering the wheels.
  function bodyPanel(lo, hi, side) {
    const shape = new THREE.Shape(); shape.moveTo(lo, .35);
    for (const cx of [-3.86, 3.23]) if (cx - .68 >= lo && cx + .68 <= hi) {
      shape.lineTo(cx - .68, .35); shape.lineTo(cx - .68, .54);
      shape.absarc(cx, .54, .68, Math.PI, 0, true); shape.lineTo(cx + .68, .35);
    }
    shape.lineTo(hi, .35); shape.lineTo(hi, 1.48); shape.lineTo(lo, 1.48); shape.closePath();
    const geometry = new THREE.ExtrudeGeometry(shape, {depth: .075, bevelEnabled: true, bevelThickness: .018, bevelSize: .018, bevelSegments: 2, steps: 1, curveSegments: 24});
    add(shell, geometry, paint, 0, 0, side > 0 ? 1.245 : -1.32);
    for (const cx of [-3.86, 3.23]) if (cx - .68 >= lo && cx + .68 <= hi) ring(shell, .677, .027, [cx, .54, side * 1.337], black, [0, 0, 0], Math.PI);
    box(shell, [hi - lo, .045, .06], [(hi + lo) / 2, 1.5, side * 1.30], white, .008);
    box(shell, [hi - lo, .095, .07], [(hi + lo) / 2, .36, side * 1.31], black, .015);
  }
  bodyPanel(-5.64, 5.57, -1);
  for (const [a, b] of [[-5.64, -.05], [1.45, 4.13], [5.37, 5.57]]) bodyPanel(a, b, 1);
  // Upper window ribbon, window gaskets and near-flush side glazing.
  for (const side of [-1, 1]) {
    box(shell, [11.29, .22, .13], [-.035, 2.87, side * 1.245], black, .06);
    box(shell, [11.29, .065, .10], [-.035, 2.99, side * 1.23], paint, .018);
    const windows = side === -1 ? [[-5.45, -4.38], [-4.30, -3.21], [-3.13, -2.04], [-1.96, -.87], [-.79, .30], [.38, 1.47], [1.55, 2.64], [2.72, 3.81], [3.89, 5.47]] : [[-5.45, -4.38], [-4.30, -3.21], [-3.13, -2.04], [-1.96, -.87], [-.79, -.12], [1.51, 2.65], [2.73, 4.07], [5.40, 5.55]];
    for (const [a, b] of windows) {
      const width = b - a;
      glazing(shell, width + .045, 1.25, [(a + b) / 2, 2.12, side * 1.295], [0, side < 0 ? Math.PI : 0, 0], black, glass);
      // A fine upper reflection band creates the laminated-glass edge.
      box(shell, [Math.max(.06, width - .09), .012, .006], [(a + b) / 2, 2.68, side * 1.315], material('reflection', '#a8c3bd', {transparent: true, opacity: .26, depthWrite: false}), .002);
      if (width > .9) {
        box(shell, [width - .06, .018, .025], [(a + b) / 2, 2.49, side * 1.319], black, .004);
        box(shell, [.078, .023, .020], [(a + b) / 2, 2.46, side * 1.318], metal, .005);
      }
    }
    for (const x of [-5.55, -4.34, -3.17, -2, -.83, 1.48, 2.68, 4.1, 5.51]) {
      if (side > 0 && x > 4.13 && x < 5.37) continue;
      box(shell, [.06, 1.36, .12], [x, 2.11, side * 1.25], black, .01);
    }
    // Green lower livery and the familiar large white SG BUS lettering.
    sign(shell, 'SG BUS', 2.38, .45, [-1.80, .98, side * 1.348], [0, side < 0 ? Math.PI : 0, 0], {bg: false, color: '#f4f8e6', weight: 750, fontSize: .84});
    sign(shell, 'AUTONOMOUS  /  ELECTRIC', 1.94, .16, [-1.80, .63, side * 1.349], [0, side < 0 ? Math.PI : 0, 0], {bg: false, color: '#264820'});
    for (const x of [-5.4, -2.9, -.35, 2.36, 5.46]) {
      if (side > 0 && (x > -.05 && x < 1.45 || x > 4.13 && x < 5.37)) continue;
      box(shell, [.095, .035, .018], [x, .59, side * 1.36], amber, .01);
    }
    for (const x of [-4.74, -2.7, 1.87]) {
      box(shell, [.012, .53, .008], [x, .89, side * 1.344], material('panelSeam', '#568d2a'), .002);
      box(shell, [.072, .026, .014], [x + .10, 1.10, side * 1.35], metal, .005);
      for (const y of [.67, 1.11]) add(shell, new THREE.SphereGeometry(.009, 6, 4), metal, x + .025, y, side * 1.352);
    }
    for (let i = 0; i < 8; i++) box(shell, [.64, .024, .024], [-4.92, 1.07 + i * .045, side * 1.35], black, .004);
  }
  accessibleSign(shell, .25, [1.79, 1.17, 1.35]);
  // Emergency releases, plug socket and flush service fittings are real geometry.
  for (const x of [.70, 4.75]) {
    box(shell, [.15, .17, .025], [x + (x > 4 ? -.73 : .84), 1.76, 1.345], black, .025);
    const release = add(shell, new THREE.CylinderGeometry(.043, .043, .012, 24), material('doorRelease', '#bd5548', {roughness: .37}), x + (x > 4 ? -.73 : .84), 1.79, 1.365); release.rotation.x = Math.PI / 2;
    sign(shell, 'OPEN', .10, .025, [x + (x > 4 ? -.73 : .84), 1.715, 1.364], [0, 0, 0], {bg: false, color: '#dce6dc'});
  }
  box(shell, [.43, .40, .027], [-5.04, .66, -1.344], black, .04);
  box(shell, [.37, .34, .028], [-5.04, .66, -1.366], paint, .033);
  sign(shell, 'ELECTRIC', .28, .063, [-5.04, .665, -1.386], [0, Math.PI, 0], {bg: false, color: '#284e2e'});
  for (const side of [-1, 1]) {
    // Blind-spot sensor pods augment the roof LiDAR; these are not extra CCTVs.
    const pod = box(shell, [.18, .10, .035], [5.10, 1.25, side * 1.357], black, .035);
    pod.name = 'Blind-spot range sensor';
    for (let j = 0; j < 3; j++) box(shell, [.008, .048, .008], [5.065 + j * .035, 1.25, side * 1.38], metal, .002);
  }

  // Sculpted front/rear caps with black window masks, LED lamps and destination boards.
  box(shell, [.44, .82, 2.47], [5.61, .78, 0], paint, .17);
  box(shell, [.39, 1.98, .14], [5.63, 2.06, 1.18], paint, .065);
  box(shell, [.39, 1.98, .14], [5.63, 2.06, -1.18], paint, .065);
  glazing(shell, 2.35, 1.37, [5.835, 2.09, 0], [0, Math.PI / 2, 0], black, glass, .074);
  box(shell, [.070, 1.23, .027], [5.864, 2.09, 0], black, .012);
  box(shell, [.27, .38, 2.40], [5.74, 2.83, 0], black, .10);
  sign(shell, 'A26   AUTONOMOUS', 2.04, .26, [5.887, 2.84, 0], [0, Math.PI / 2, 0], {color: '#ffc65d', led: true, fontSize: .7});
  box(shell, [.10, .22, 2.04], [5.89, 1.36, 0], black, .035);
  sign(shell, 'BUS / TECH', .72, .14, [5.951, 1.37, 0], [0, Math.PI / 2, 0], {color: '#e6f5df'});
  sign(shell, 'SG 026', .46, .10, [5.90, .60, 0], [0, Math.PI / 2, 0], {bg: '#e9d686', color: '#283a33'});
  box(shell, [.17, .11, 2.32], [5.88, .39, 0], black, .05);
  for (const side of [-1, 1]) {
    box(shell, [.075, .29, .43], [5.865, 1.01, side * .88], black, .035);
    box(shell, [.018, .05, .32], [5.912, 1.10, side * .88], light, .018);
    box(shell, [.020, .12, .07], [5.913, 1.035, side * 1.023], light, .016);
    box(shell, [.02, .035, .25], [5.915, .915, side * .875], amber, .01);
    rod(shell, [5.913, 1.51, side * .09], [5.93, 2.03, side * .50], .012, black);
    rod(shell, [5.93, 2.03, side * .50], [5.938, 2.31, side * .72], .021, black);
  }
  box(shell, [.38, 1.04, 2.45], [-5.64, .96, 0], paint, .15);
  glazing(shell, 2.33, 1.39, [-5.825, 2.13, 0], [0, -Math.PI / 2, 0], black, glass, .10);
  box(shell, [.28, .30, 2.38], [-5.73, 2.89, 0], paint, .08);
  sign(shell, 'A26', .49, .22, [-5.866, 2.72, .60], [0, -Math.PI / 2, 0], {color: '#ffc65d', led: true});
  sign(shell, 'ZERO EMISSIONS', 1.42, .19, [-5.856, 1.16, 0], [0, -Math.PI / 2, 0], {bg: '#7bbc27'});
  for (const side of [-1, 1]) {
    box(shell, [.026, .64, .15], [-5.86, .86, side * 1.01], black, .012);
    box(shell, [.033, .40, .066], [-5.879, .91, side * 1.012], red, .02);
  }
  box(shell, [.04, .065, 1.45], [-5.886, .41, 0], black, .025);
  sign(shell, 'SG 026', .46, .10, [-5.889, .55, 0], [0, -Math.PI / 2, 0], {bg: '#e9d686', color: '#283a33'});
  for (let j = 0; j < 12; j++) box(shell, [.02, .024, 1.2], [-5.855, .73 + j * .026, 0], black, .006);
  for (const z of [-.86, -.44, .44, .86]) {
    const sensor = add(shell, new THREE.CylinderGeometry(.019, .019, .016, 16), black, -5.865, .64, z); sensor.rotation.z = Math.PI / 2;
  }

  // Four realistic wheels; rims are recessed inside thick rubber sidewalls.
  const roadWheels = [];
  for (const x of [-3.86, 3.23]) for (const side of [-1, 1]) {
    const wheel = new THREE.Group(); chassis.add(wheel); wheel.position.set(x, .53, side * 1.19);
    wheel.name = `Road wheel ${x} ${side}`;
    roadWheels.push(wheel);
    const tire = add(wheel, new THREE.CylinderGeometry(.535, .535, .30, 56), rubber); tire.rotation.x = Math.PI / 2;
    ring(wheel, .42, .093, [0, 0, side * .155], rubber);
    ring(wheel, .484, .006, [0, 0, side * .178], material('tireLettering', '#48534f', {roughness: .88, metalness: 0}));
    ring(wheel, .375, .006, [0, 0, side * .179], rubber);
    const rim = add(wheel, new THREE.CylinderGeometry(.346, .346, .075, 48), metal, 0, 0, side * .17); rim.rotation.x = Math.PI / 2;
    ring(wheel, .31, .025, [0, 0, side * .216], white);
    for (let n = 0; n < 10; n++) {
      const a = n / 10 * Math.PI * 2;
      const hole = add(wheel, new THREE.CylinderGeometry(.047, .047, .006, 12), black, Math.cos(a) * .237, Math.sin(a) * .237, side * .218); hole.rotation.x = Math.PI / 2;
      const nut = add(wheel, new THREE.CylinderGeometry(.016, .016, .020, 6), metal, Math.cos(a) * .121, Math.sin(a) * .121, side * .251); nut.rotation.x = Math.PI / 2;
    }
    const hub = add(wheel, new THREE.CylinderGeometry(.10, .11, .085, 32), metal, 0, 0, side * .234); hub.rotation.x = Math.PI / 2;
    const hubCap = add(wheel, new THREE.CylinderGeometry(.074, .074, .008, 32), black, 0, 0, side * .282); hubCap.rotation.x = Math.PI / 2;
    for (let n = 0; n < 44; n++) {
      const a = n / 44 * Math.PI * 2;
      const tread = box(wheel, [.025, .020, .29], [Math.cos(a) * .531, Math.sin(a) * .531, 0], material('tread', '#252f34'), .006); tread.rotation.z = a - Math.PI / 2;
    }
    consolidate(wheel);
    box(fixed, [1.30, .24, .37], [x, .75, side * 1.06], floor, .11);
    box(shell, [.13, .32, .31], [x - .62, .30, side * 1.17], rubber, .009);
  }

  // Two pairs of powered sliding door leaves. Each leaf moves outward, then apart.
  const doors = [];
  for (const [name, x, width] of [['front', 4.75, 1.20], ['middle', .70, 1.46]]) {
    box(fixed, [width, .04, .38], [x, .607, 1.17], material('threshold', '#d4bd66'), .008);
    for (let j = 0; j < 8; j++) box(fixed, [width - .08, .006, .012], [x, .632, 1.01 + j * .041], black, .003);
    box(fixed, [width - .13, .017, .018], [x, .62, 1.365], light, .006);
    for (const end of [-1, 1]) rod(fixed, [x + end * (width / 2 + .025), .6, 1.13], [x + end * (width / 2 + .025), 2.78, 1.13], .027, yellow);
    const leaves = [];
    for (const direction of [-1, 1]) {
      const group = new THREE.Group(); group.position.set(x + direction * width / 4, 0, 1.31); chassis.add(group);
      const w = width / 2 - .025;
      glazing(group, w, 2.12, [0, 1.67, 0], [0, 0, 0], black, glass, .043);
      box(group, [w - .06, .38, .032], [0, .875, .05], paint, .01);
      box(group, [w - .07, .018, .04], [0, 1.08, .01], black, .005);
      box(group, [.030, 2.04, .055], [-direction * (w / 2 - .008), 1.67, .01], rubber, .009);
      rod(group, [-direction * w * .28, 1.29, -.06], [-direction * w * .28, 1.97, -.06], .019, yellow);
      box(group, [w - .08, .023, .007], [0, 1.22, .069], yellow, .003);
      sign(group, name === 'front' ? 'ENTRY' : 'ENTRY / EXIT', w - .08, .11, [0, 2.52, .07], [0, 0, 0], {fontSize: .6});
      consolidate(group); leaves.push({group, direction, rest: group.position.x});
    }
    if (name !== 'front') sign(shell, 'ACCESSIBLE ENTRY', width - .1, .13, [x, 2.92, 1.322], [0, 0, 0], {color: '#d5e5cf'});
    for (const direction of [-1, 1]) box(shell, [.17, .022, .025], [x + direction * (width / 2 - .13), 2.78, 1.322], amber, .009);
    doors.push({name, leaves, width});
  }

  const ramp = new THREE.Group(); chassis.add(ramp); ramp.position.set(.70, .59, 1.30); ramp.rotation.x = .192;
  const rampDeck = new THREE.Group(); ramp.add(rampDeck);
  box(rampDeck, [1.14, .05, 2.09], [0, -.005, 1.045], material('ramp', '#9faeaa', {roughness: .68, metalness: .5}), .018);
  for (const x of [-.553, .553]) box(rampDeck, [.036, .065, 2.09], [x, .017, 1.045], yellow, .009);
  box(rampDeck, [1.10, .01, .11], [0, .028, 2.02], yellow, .005);
  for (let n = 0; n < 19; n++) box(rampDeck, [1.01, .008, .018], [0, .026, .09 + n * .102], material('rampGrooves', '#768b88'), .003);
  accessibleSign(rampDeck, .44, [0, .034, .83], [-Math.PI / 2, 0, 0], true);
  consolidate(rampDeck); ramp.scale.z = .001; ramp.visible = false;

  // Sculpted seat shells, padded upholstery, seat numbers and grab handles.
  const seats = [];
  const clothMap = texture((ctx, w, h) => {
    ctx.fillStyle = '#e0e4e1'; ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = '#d0d7d2';
    for (let x = 0; x < w; x += 4) ctx.fillRect(x, 0, 1, h);
    for (let y = 0; y < h; y += 4) ctx.fillRect(0, y, w, 1);
    for (let i = 0; i < 960; i++) { const x = (i * 73) % w, y = (i * 131) % h; ctx.fillStyle = i % 3 ? '#eaf0eb' : '#bfcac4'; ctx.fillRect(x, y, 2, 1); }
  }, 128, 128); clothMap.wrapS = clothMap.wrapT = THREE.RepeatWrapping; clothMap.repeat.set(3, 3);
  for (const spec of SEATS) {
    const group = new THREE.Group(); group.position.set(spec.x, spec.y, spec.z); group.name = spec.id; group.userData.seat = spec; seatsGroup.add(group);
    const upholstery = new THREE.MeshStandardMaterial({color: spec.type === 'priority' ? '#e4a55b' : '#548ea8', roughness: .86, metalness: 0, map: clothMap});
    rod(group, [0, .02, 0], [0, .37, 0], .043, metal);
    box(group, [.28, .035, .30], [0, .025, 0], black, .022);
    box(group, [.50, .08, .44], [0, .365, 0], cream, .05);
    box(group, [.44, .077, .395], [.014, .418, 0], upholstery, .036);
    for (const z of [-.183, .183]) {
      box(group, [.38, .033, .018], [.014, .455, z], upholstery, .008);
      rod(group, [-.12, .461, z], [.15, .461, z], .0035, material(spec.type === 'priority' ? 'priorityPiping' : 'standardPiping', spec.type === 'priority' ? '#b58247' : '#436f83', {roughness: .93, metalness: 0}));
    }
    const back = box(group, [.095, .65, .445], [-.225, .70, 0], cream, .04); back.rotation.z = .11;
    const cushion = box(group, [.065, .52, .382], [-.160, .735, 0], upholstery, .029); cushion.rotation.z = .11;
    for (const z of [-.176, .176]) {
      const bolster = box(group, [.050, .46, .021], [-.126, .736, z], upholstery, .010); bolster.rotation.z = .11;
    }
    box(group, [.025, .050, .335], [-.292, .505, 0], cream, .016);
    rod(group, [-.28, .255, -.16], [-.28, .255, .16], .014, metal);
    if (Math.abs(spec.z) < .7) {
      const aisleSide = -Math.sign(spec.z);
      rod(group, [-.14, .46, aisleSide * .223], [-.14, .66, aisleSide * .223], .015, metal);
      box(group, [.35, .045, .049], [.00, .682, aisleSide * .223], black, .020);
    }
    rod(group, [-.28, .9, -.17], [-.28, 1.06, -.17], .018, yellow);
    rod(group, [-.28, 1.06, -.17], [-.28, 1.06, .17], .018, yellow);
    rod(group, [-.28, .9, .17], [-.28, 1.06, .17], .018, yellow);
    sign(group, spec.id, .155, .066, [-.316, .76, 0], [0, -Math.PI / 2, 0], {bg: spec.type === 'priority' ? '#a4622c' : '#24566f', color: '#f4f4df'});
    consolidate(group); seats.push({group, spec, upholstery});
  }
  // Ceiling-height grab rails, priority-area poles and hanging straps.
  for (const z of [-.44, .44]) rod(fixed, [-5.26, 2.62, z], [5.16, 2.62, z], .026, yellow);
  for (const x of [-4.25, -2.47, -.54, 2.18, 5.16]) {
    rod(fixed, [x, .65, -.30], [x, 2.62, -.30], .025, yellow);
    rod(fixed, [x, 2.62, -.44], [x, 2.62, .44], .025, yellow);
  }
  for (const x of [-4.8, -3.5, -1.6, 1.8, 3.2, 4.5]) for (const z of [-.43, .43]) {
    rod(fixed, [x, 2.62, z], [x, 2.38, z], .012, black);
    ring(fixed, .077, .012, [x, 2.30, z], yellow, [0, Math.PI / 2, 0]);
  }
  sign(fixed, 'PRIORITY SEATING', 1.6, .20, [3.92, 2.61, -1.17], [0, 0, 0], {bg: '#d4a95f', color: '#322d21'});
  // The front is a full passenger space with an information screen, not a cockpit.
  box(fixed, [.07, .34, 1.08], [5.35, 2.40, 0], black);
  sign(fixed, 'NEXT STOP  •  EVERYONE ABOARD', 1.02, .25, [5.302, 2.41, 0], [0, -Math.PI / 2, 0], {color: '#c9f192', fontSize: .42});
  rod(fixed, [5.33, .62, -.85], [5.33, 2.56, -.85], .029, yellow);
  rod(fixed, [5.33, 1.21, -.85], [5.33, 1.21, .30], .029, yellow);
  // RFID reader at the front entrance.
  const rfid = new THREE.Group(); fixed.add(rfid); rfid.position.set(4.15, 1.27, .95);
  box(rfid, [.15, .27, .18], [0, 0, 0], black);
  box(rfid, [.017, .17, .13], [.085, .01, 0], material('rfidScreen', '#c5f79c', {emissive: '#91cf60', emissiveIntensity: .35}), .008);
  sign(rfid, 'TAP', .11, .056, [.097, .01, 0], [0, Math.PI / 2, 0], {bg: '#c5f79c', color: '#283c29'});

  // Roof extrusion, electric HVAC and one LiDAR pod (distinct from the two CCTVs).
  box(roof, [11.46, .22, 2.48], [-.04, 3.035, 0], white, .105);
  box(roof, [11.27, .065, 2.23], [-.04, 2.91, 0], cream, .03);
  box(roof, [3.10, .23, 1.68], [-2.3, 3.245, 0], white, .11);
  for (const x of [-3.07, -1.58]) {
    const fan = add(roof, new THREE.CylinderGeometry(.47, .47, .025, 40), black, x, 3.367, 0);
    for (let j = 1; j < 5; j++) ring(roof, j * .09, .008, [x, 3.384, 0], metal, [Math.PI / 2, 0, 0]);
    for (let a = 0; a < 4; a++) { const blade = box(roof, [.81, .012, .032], [x, 3.392, 0], metal, .003); blade.rotation.y = a * Math.PI / 4; }
  }
  for (let j = 0; j < 12; j++) box(roof, [.048, .042, 1.20], [-.26 + j * .09, 3.176, 0], black, .007);
  add(roof, new THREE.CylinderGeometry(.22, .26, .13, 40), black, 4.87, 3.22, 0);
  add(roof, new THREE.CylinderGeometry(.22, .22, .09, 40), material('lidar', '#73afae', {metalness: .6, roughness: .15}), 4.87, 3.33, 0);
  for (const z of [-.78, .78]) box(roof, [10.65, .016, .034], [0, 2.867, z], light, .009);
  for (const z of [-1.02, 1.02]) {
    box(roof, [10.48, .035, .15], [-.03, 2.863, z], material('ceilingVent', '#657773', {roughness: .65}), .01);
    for (let j = 0; j < 88; j++) box(roof, [.008, .012, .13], [-5.17 + j * .119, 2.839, z], cream, .002);
  }
  for (const x of [-4.5, -.3, 3.75]) {
    box(roof, [.38, .022, .32], [x, 2.859, 0], cream, .035);
    const speaker = add(roof, new THREE.CylinderGeometry(.080, .080, .009, 24), black, x, 2.843, 0);
    for (let j = 1; j < 4; j++) ring(roof, j * .019, .0035, [x, 2.836, 0], metal, [Math.PI / 2, 0, 0]);
  }
  // Maintenance hatch, emergency roof escape and longitudinal drainage seams.
  box(roof, [1.10, .044, .87], [1.80, 3.168, 0], black, .06);
  box(roof, [1.015, .033, .78], [1.80, 3.195, 0], material('hatchGlass', '#7c9690', {metalness: .55, roughness: .17}), .046);
  for (const x of [1.40, 2.20]) box(roof, [.12, .036, .052], [x, 3.225, -.30], metal, .009);
  for (const z of [-1.07, 1.07]) box(roof, [10.65, .018, .026], [-.12, 3.15, z], metal, .007);
  for (const x of [-4.57, -.51, 3.28]) {
    box(roof, [.018, .016, 2.08], [x, 3.151, 0], material('roofSeam', '#96aaa3', {roughness: .53}), .004);
  }
  // Raised escape hatch lining is also readable from within the cabin.
  box(roof, [1.12, .023, .85], [1.80, 2.855, 0], black, .035);
  box(roof, [1.02, .025, .75], [1.80, 2.837, 0], cream, .026);
  rod(roof, [1.62, 2.80, -.20], [1.95, 2.80, -.20], .015, material('escapeHandle', '#af5949'));

  const cameras = {};
  function cameraUnit(name, spec) {
    const position = V(...spec.position), target = V(...spec.target), fov = spec.fov;
    const group = new THREE.Group(); group.position.copy(position); group.lookAt(target); fixed.add(group);
    group.name = `${name === 'outside' ? 'Mid-door exterior' : 'Interior aisle'} CCTV`;
    box(group, [.22, .13, .35], [0, 0, 0], white, .038);
    box(group, [.235, .026, .22], [0, .069, .084], white, .012);
    box(group, [.15, .085, .018], [0, -.006, .18], black, .008);
    const lens = add(group, new THREE.CylinderGeometry(.036, .036, .033, 24), material('lens', '#48b8a3', {emissive: '#258d7d', emissiveIntensity: .5, metalness: .7, roughness: .1}), 0, -.006, .196); lens.rotation.x = Math.PI / 2;
    ring(group, .039, .006, [0, -.006, .213], black);
    for (const x of [-.058, .058]) for (const y of [-.023, .020]) add(group, new THREE.SphereGeometry(.005, 6, 4), material('cameraIR', '#78999a', {roughness: .24, metalness: .6}), x, y, .194);
    rod(fixed, [position.x, position.y + .02, position.z], [position.x, position.y + .19, position.z - .06], .027, metal);
    const camera = makeCctvCamera(spec); chassis.add(camera);
    cameras[name] = {camera, position: position.clone(), target: target.clone(), fov, aspect: camera.aspect};
  }
  for (const [name, spec] of Object.entries(CAMERA_LAYOUT)) cameraUnit(name, spec);
  sign(shell, '01', .12, .073, [2.725, 2.55, 1.36], [0, 0, 0], {bg: '#142e34', color: '#baf2df'});

  consolidate(fixed); consolidate(shell); consolidate(roof);
  // Add after consolidation to retain the dynamic texture and identifiable
  // enclosure; it still fades with the shell in the interior cutaway.
  const entranceLed = entranceDisplay(shell);
  // Each cutaway group owns its material copies; fading cannot dim cabin materials.
  const fadeGroups = [shell, roof, ...doors.flatMap(d => d.leaves.map(l => l.group))];
  for (const group of fadeGroups) group.traverse(obj => {
    if (!obj.isMesh) return;
    obj.material = obj.material.clone(); obj.material.userData.baseOpacity = obj.material.opacity;
    obj.material.userData.baseTransparent = obj.material.transparent;
  });

  const coverage = new THREE.Group(); coverage.name = 'Two CCTV coverage volumes'; chassis.add(coverage); coverage.visible = false;
  for (const [name, {camera, target}] of Object.entries(cameras)) {
    const position = camera.position;
    const direction = target.clone().sub(position).normalize();
    const right = direction.clone().cross(V(0, 1, 0)).normalize(), up = right.clone().cross(direction).normalize();
    // Exact perspective frustum, using the same lens and aspect as the feed.
    const depth = name === 'inside' ? 6.7 : 3.8, hh = Math.tan(camera.fov * Math.PI / 360) * depth, hw = hh * camera.aspect;
    const center = position.clone().addScaledVector(direction, depth), points = [position];
    for (const [x, y] of [[-1,-1],[1,-1],[1,1],[-1,1]]) points.push(center.clone().addScaledVector(right, x * hw).addScaledVector(up, y * hh));
    const coordinates = [];
    for (const [a,b,c] of [[0,1,2],[0,2,3],[0,3,4],[0,4,1]]) coordinates.push(...points[a].toArray(),...points[b].toArray(),...points[c].toArray());
    const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(coordinates, 3)); geometry.computeVertexNormals();
    const color = name === 'inside' ? '#efb86d' : '#9eedc3';
    const volume = add(coverage, geometry, new THREE.MeshBasicMaterial({color, transparent: true, opacity: .065, side: THREE.DoubleSide, depthWrite: false})); volume.castShadow = false;
    const lines = [];
    for (let i = 1; i <= 4; i++) lines.push(points[0], points[i], points[i], points[i % 4 + 1]);
    coverage.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(lines), new THREE.LineBasicMaterial({color, transparent: true, opacity: .36, depthWrite: false})));
  }

  let cutaway = 0;
  function setCutaway(value) {
    cutaway = clamp(value); roof.position.y = cutaway * .80;
    for (const group of fadeGroups) {
      group.visible = cutaway < .997;
      group.traverse(obj => { if (!obj.isMesh) return;
        obj.material.opacity = obj.material.userData.baseOpacity * (1 - cutaway);
        const transparent = obj.material.userData.baseTransparent || cutaway > .005;
        if (obj.material.transparent !== transparent) { obj.material.transparent = transparent; obj.material.needsUpdate = true; }
        obj.castShadow = cutaway < .2 && !obj.material.userData.baseTransparent;
      });
    }
  }
  function setDoors(value, which = 'both') {
    for (const door of doors) for (const leaf of door.leaves) {
      const amount = which === 'both' || which === door.name ? value : 0;
      leaf.group.position.x = leaf.rest + leaf.direction * (door.width * .39) * smooth(amount);
      leaf.group.position.z = 1.31 + .14 * smooth(Math.min(1, amount * 4));
    }
  }
  function setRamp(value) {
    ramp.visible = value > .001; ramp.scale.z = Math.max(.001, value);
  }
  function highlight(id = null, type = null) {
    for (const seat of seats) {
      const selected = seat.spec.id === id || seat.spec.type === type;
      seat.upholstery.emissive.set(selected ? (seat.spec.type === 'priority' ? '#e99535' : '#69cbd8') : '#000000');
      seat.upholstery.emissiveIntensity = selected ? .35 : 0;
    }
  }
  function setTravel(distance) {
    if (!Number.isFinite(distance)) return;
    for (const wheel of roadWheels) wheel.rotation.z = -(distance / .535) % (Math.PI * 2);
  }
  return {chassis, seats, cameras, coverage, entranceLed, setEntranceDisplay: entranceLed.update,
    setCutaway, setDoors, setRamp, setTravel, highlight, getCutaway: () => cutaway};
}

export function buildStop(scene) {
  const group = new THREE.Group(); scene.add(group); group.name = 'Presentation bus stop';
  const asphalt = material('asphalt', '#35474c', {roughness: .98, metalness: 0});
  box(group, [200, .12, 200], [0, -.12, 0], asphalt, .001);
  box(group, [14.2, .19, 2.45], [-.15, .015, 4.48], material('pavement', '#8d9c99', {roughness: .95}), .07);
  for (let i = 0; i < 19; i++) box(group, [.73, .20, .16], [-6.90 + i * .75, .02, 3.245], material(i % 2 ? 'curbGray' : 'curbLight', i % 2 ? '#637671' : '#b9c2b6', {roughness: .9}), .015);
  const tactile = material('tactile', '#ccaa5a', {roughness: .9});
  box(group, [5.7, .014, .32], [2.35, .118, 3.56], tactile, .008);
  // Small tactile studs remain low-poly and are consolidated into one draw call.
  for (let i = 0; i < 53; i++) for (let j = 0; j < 3; j++) add(group, new THREE.SphereGeometry(.018, 6, 4), tactile, -.4 + i * .105, .13, 3.46 + j * .09);
  const roadInk = material('roadInk', '#b4b992', {roughness: .95});
  for (let x = -9; x < 10; x += 2.25) box(group, [1.2, .005, .05], [x, -.052, -2.65], roadInk, .003);
  for (const z of [2.46, 2.61]) box(group, [14, .004, .04], [0, -.051, z], material('roadYellow', '#b9a258', {roughness: .94}), .002);
  // A restrained Singapore bus-stop totem gives the vehicle a real boarding edge.
  const pole = material('stopPole', '#c7d4cd', {roughness: .4, metalness: .55});
  rod(group, [-4.9, .10, 4.75], [-4.9, 2.68, 4.75], .044, pole);
  box(group, [.055, .61, .64], [-4.9, 2.55, 4.75], material('stopGreen', '#7bbc27'), .04);
  sign(group, 'BUS', .51, .27, [-4.864, 2.66, 4.75], [0, Math.PI / 2, 0], {bg: '#7bbc27', color: '#15251b', fontSize: .8});
  sign(group, 'A26', .48, .17, [-4.863, 2.39, 4.75], [0, Math.PI / 2, 0], {bg: '#7bbc27', color: '#15251b'});
  box(group, [.07, .66, .39], [-4.9, 1.57, 4.75], pole, .025);
  sign(group, 'BUS TECH', .33, .12, [-4.855, 1.77, 4.75], [0, Math.PI / 2, 0], {bg: '#c7d4cd', color: '#263c39'});
  for (let n = 0; n < 5; n++) box(group, [.007, .012, .25], [-4.853, 1.59 - n * .045, 4.75], material('scheduleInk', '#52766f'), .001);
  consolidate(group); return group;
}

export function buildPassenger(scene, kind) {
  const root = new THREE.Group(); root.name = `Demonstration passenger: ${kind}`; scene.add(root);
  const skin = material('skin', '#c59a7a', {roughness: .83, metalness: 0});
  const trousers = material('trousers', '#263c51', {roughness: .88, metalness: 0});
  const jacket = material(`jacket-${kind}`, kind === 'senior' ? '#d49b58' : kind === 'pram' ? '#839e8e' : '#75a9a7', {roughness: .87, metalness: 0});
  const hair = material('hair', '#39403e', {roughness: 1, metalness: 0});
  const shoes = material('shoes', '#e1e3d4', {roughness: .83, metalness: 0});
  const silver = materials.metal, black = materials.black;
  const human = new THREE.Group(); root.add(human);
  const upper = new THREE.Group(); human.add(upper);
  const torso = add(upper, new THREE.CapsuleGeometry(.205, .28, 6, 14), jacket, 0, 1.11, 0); torso.scale.z = .80;
  add(upper, new THREE.CylinderGeometry(.065, .08, .13, 12), skin, 0, 1.43, 0);
  const head = add(upper, new THREE.SphereGeometry(.15, 20, 16), skin, 0, 1.60, 0); head.scale.set(.88, 1.10, .9);
  const hairMesh = add(upper, new THREE.SphereGeometry(.152, 20, 12, 0, Math.PI * 2, 0, Math.PI * .49), hair, -.018, 1.642, 0); hairMesh.scale.z = .91;
  add(upper, new THREE.SphereGeometry(.032, 10, 8), skin, .132, 1.59, 0);
  const legs = [], arms = [];
  function joint(parent, position) { const g = new THREE.Group(); g.position.set(...position); parent.add(g); return g; }
  for (const side of [-1, 1]) {
    const hip = joint(human, [0, .84, side * .103]);
    add(hip, new THREE.CapsuleGeometry(.084, .25, 5, 10), trousers, 0, -.19, 0);
    const knee = joint(hip, [0, -.38, 0]);
    add(knee, new THREE.CapsuleGeometry(.067, .25, 5, 10), trousers, 0, -.18, 0);
    box(knee, [.25, .095, .13], [.059, -.38, 0], shoes, .045);
    legs.push({hip, knee, side});
    const shoulder = joint(upper, [0, 1.29, side * .20]);
    add(shoulder, new THREE.CapsuleGeometry(.061, .19, 5, 10), jacket, 0, -.15, 0);
    const elbow = joint(shoulder, [0, -.29, 0]);
    add(elbow, new THREE.CapsuleGeometry(.048, .18, 5, 10), skin, 0, -.135, 0);
    add(elbow, new THREE.SphereGeometry(.053, 10, 8), skin, 0, -.26, 0);
    arms.push({shoulder, elbow, side});
  }
  const rolling = [];
  if (kind === 'wheelchair') {
    for (const side of [-1, 1]) {
      const wheel = new THREE.Group(); root.add(wheel); wheel.position.set(-.05, .32, side * .32); rolling.push(wheel);
      ring(wheel, .296, .028, [0, 0, 0], materials.rubber); ring(wheel, .261, .012, [0, 0, side * .036], silver);
      for (let n = 0; n < 12; n++) { const a = n / 12 * Math.PI * 2; rod(wheel, [0, 0, 0], [Math.cos(a) * .279, Math.sin(a) * .279, 0], .006, silver); }
      rod(root, [-.21, .32, side * .25], [.43, .17, side * .25], .018, silver);
      rod(root, [-.21, .32, side * .25], [-.22, .96, side * .25], .019, silver);
      rod(root, [-.22, .96, side * .25], [-.37, .96, side * .25], .023, black);
      rod(root, [-.16, .69, side * .27], [.28, .69, side * .27], .023, black);
      ring(root, .072, .02, [.43, .085, side * .25], materials.rubber);
    }
    box(root, [.45, .042, .46], [.05, .50, 0], black);
    box(root, [.055, .40, .44], [-.22, .73, 0], trousers);
    box(root, [.20, .035, .47], [.54, .13, 0], black, .02);
  }
  if (kind === 'pram') {
    human.position.x = -.65;
    const stroller = new THREE.Group(); root.add(stroller); stroller.position.x = .52;
    for (const side of [-1, 1]) {
      rod(stroller, [-.34, .1, side * .26], [.23, .70, side * .21], .018, silver);
      rod(stroller, [.34, .1, side * .26], [-.30, .93, side * .21], .018, silver);
      ring(stroller, .092, .025, [-.34, .11, side * .26], materials.rubber);
      ring(stroller, .092, .025, [.34, .11, side * .26], materials.rubber);
    }
    rod(stroller, [-.30, .93, -.22], [-.30, .93, .22], .025, black);
    box(stroller, [.65, .26, .45], [0, .54, 0], jacket, .09);
    const hood = add(stroller, new THREE.SphereGeometry(.31, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), jacket, -.14, .72, 0); hood.scale.set(.92, 1, .82);
    box(stroller, [.32, .07, .35], [.1, .67, 0], creamMaterial(), .025);
  }
  if (kind === 'walking') {
    for (const side of [-1, 1]) {
      rod(root, [.32, .03, side * .31], [.36, .76, side * .28], .019, silver);
      rod(root, [.76, .03, side * .31], [.68, .76, side * .28], .019, silver);
      rod(root, [.36, .76, side * .28], [.68, .76, side * .28], .022, black);
      box(root, [.07, .04, .07], [.76, .03, side * .31], black, .01);
    }
    rod(root, [.68, .59, -.28], [.68, .59, .28], .018, silver);
  }
  if (kind === 'senior') {
    rod(root, [.33, .03, .30], [.35, .77, .30], .017, silver);
    rod(root, [.35, .77, .30], [.48, .77, .30], .023, black);
    box(root, [.10, .065, .012], [.20, 1.03, -.29], material('card', '#e1c784'), .006);
  }
  function pose(time, walking = 0, seated = 0) {
    const sit = kind === 'wheelchair' ? 1 : seated;
    human.position.y = -.32 * sit;
    for (const {hip, knee, side} of legs) {
      hip.rotation.z = Math.sin(time * 5.5 + (side > 0 ? Math.PI : 0)) * .33 * walking * (1 - sit) + sit * Math.PI / 2;
      knee.rotation.z = Math.max(0, Math.sin(time * 5.5 + (side > 0 ? Math.PI : 0))) * -.42 * walking * (1 - sit) - sit * Math.PI / 2;
    }
    for (const {shoulder, elbow, side} of arms) {
      shoulder.rotation.z = kind === 'pram' || kind === 'walking' ? .45 : sit ? .20 : -Math.sin(time * 5.5 + (side > 0 ? Math.PI : 0)) * .23 * walking;
      elbow.rotation.z = sit ? .92 : kind === 'pram' || kind === 'walking' ? .70 : .13;
    }
    upper.position.y = Math.sin(time * 11) * .012 * walking * (1 - sit);
    for (const wheel of rolling) wheel.rotation.z = -time * walking * 2;
  }
  pose(0); return {root, pose};
}
function creamMaterial() { return materials.cream; }
