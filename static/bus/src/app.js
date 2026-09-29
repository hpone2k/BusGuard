import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {buildBus} from './model.js';
import {cabinPresentation} from './showcase-state.js';
import {controllerPresentation, scenarioPresentation, serviceLink} from './controller-state.js';
import {createRenderLoop, renderPixelRatio} from './render-loop.js';
import {createTravelMotion, travelPresentation, observationAge, shouldWakeTravel} from './travel-motion.js';
import {buildRoad} from './road.js';
import {mountMessagePanel} from './message-panel.js';
import {entranceLedPresentation} from './entrance-led.js';
import {SpokenGuidance} from '../../passenger/spoken-guidance.js';
import {AnnouncementFeed} from '../../announcement-feed.js';

const $ = id => document.getElementById(id);
const messagePanel = mountMessagePanel();
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
let snapshot = null, receivedAt = 0, online = false, stopped = false;
let model = null;
let renderKey = '', messageKey = '', controllerKey = '';
let scenarioKey = '';
const ANNOUNCEMENTS_KEY = 'bustech.bus.announcements.v1';
let voiceEnabled = false, voiceRevision = 0;
try { voiceEnabled = localStorage.getItem(ANNOUNCEMENTS_KEY) === 'on'; } catch { /* Device preference is optional. */ }
const busSpeaker = new SpokenGuidance();
const announcementFeed = new AnnouncementFeed();
let pollTimer = null, pollAbort = null, polling = false;
let syncScene = () => {}, suspendScene = () => {}, resumeScene = () => {}, disposeScene = () => {};
const display = value => value === null ? '—' : String(value);
$('retryGraphics').onclick = () => location.reload();

function renderAnnouncementControl() {
  const button = $('announcements');
  button.setAttribute('aria-pressed', String(voiceEnabled));
  button.setAttribute('aria-label', `${voiceEnabled ? 'Mute' : 'Unmute'} bus announcements on this device`);
  button.title = `${voiceEnabled ? 'Mute' : 'Unmute'} announcements · this device only`;
  $('announcementLabel').textContent = voiceEnabled ? 'Sound on' : 'Muted';
}
function announcementUnavailable(result, revision) {
  if (!voiceEnabled || revision !== voiceRevision || !result?.unavailable) return;
  if (result.reason === 'playback') {
    // A restored preference cannot override a browser's autoplay policy.
    // Offer another deliberate tap without changing the saved preference.
    voiceEnabled = false; voiceRevision++; busSpeaker.cancel(); renderAnnouncementControl();
    $('announcementLabel').textContent = 'Enable sound';
    $('announcements').title = 'Tap to allow announcements on this device';
  } else $('announcements').title = 'AI audio is unavailable. Please follow the on-screen message.';
}

function announce(controller) {
  const announcements = announcementFeed.take(snapshot?.assistance, controller.connected);
  if (!voiceEnabled || document.hidden) return;
  const revision = voiceRevision;
  for (const announcement of announcements) busSpeaker.speak(announcement.message, {expiresMs: 30000})
    .then(result => announcementUnavailable(result, revision)).catch(() => announcementUnavailable({unavailable: true}, revision));
}
renderAnnouncementControl();
if ('Audio' in window) {
  $('announcements').onclick = () => {
    voiceEnabled = !voiceEnabled; voiceRevision++; announcementFeed.reset();
    try { localStorage.setItem(ANNOUNCEMENTS_KEY, voiceEnabled ? 'on' : 'off'); } catch { /* Still works without storage. */ }
    renderAnnouncementControl();
    if (voiceEnabled) announce(controllerPresentation(snapshot, observationAge(performance.now(), receivedAt), online));
    else busSpeaker.cancel();
  };
} else { voiceEnabled = false; renderAnnouncementControl(); $('announcements').disabled = true; $('announcements').title = 'Voice announcements are unavailable in this browser'; }

function updatePanel() {
  const now = performance.now();
  const age = observationAge(now, receivedAt);
  let view = cabinPresentation(snapshot, age, online);
  const controller = controllerPresentation(snapshot, age, online);
  announce(controller);
  const journey = scenarioPresentation(snapshot?.assistance, age, online);
  const journeyKey = JSON.stringify(journey);
  if (journeyKey !== scenarioKey) {
    scenarioKey = journeyKey;
    $('journeyState').textContent = journey.title;
    $('journeyState').dataset.phase = journey.phase;
    $('boardingTimer').textContent = journey.timer;
    $('boardingTimer').title = journey.timerLabel;
    $('boardingTimer').setAttribute('aria-label', `${journey.timerLabel}: ${journey.timer}`);
    $('priorityRemaining').textContent = display(journey.priority);
    $('standardRemaining').textContent = display(journey.standard);
    $('seatCapacity').textContent = journey.occupied === null ? 'Seat assignments unavailable'
      : `${journey.occupied} / 26 aboard${journey.reserved ? ` · ${journey.reserved} reserved` : ''} · ${journey.estimated ? 'estimated seats' : 'simulated'}`;
    $('seatCapacity').title = journey.seatBasis || 'Simulated seat assignments.';
    $('journeyDetail').textContent = journey.detail;
    $('seatAvailability').dataset.warning = String(journey.warning || journey.available === 0);
    $('connection').dataset.state = journey.connected ? 'live' : 'stale';
    $('connectionText').textContent = journey.connected ? 'Connected' : 'Offline';
  }
  const key = JSON.stringify(view);
  if (key !== renderKey) {
    renderKey = key;
    $('passengerCount').textContent = display(view.people);
    $('countDetail').textContent = view.detail;
    $('countNote').textContent = view.note;
    $('typeCount').textContent = `${display(view.typeCount)} ${view.typeCount === 1 ? 'type' : 'types'}`;
    $('typeList').replaceChildren(...view.types.map(type => {
      const row = document.createElement('div'), name = document.createElement('span');
      const dot = document.createElement('i'); dot.className = 'type-dot';
      name.append(dot, document.createTextNode(type.name));
      const count = document.createElement('b'); count.textContent = display(type.count);
      row.append(name, count); return row;
    }));
  }
  if (controller.message !== messageKey) { messageKey = controller.message; $('busMessage').textContent = controller.message; }
  if (controller.detail !== controllerKey) { controllerKey = controller.detail; $('simulationState').textContent = controller.detail; }
  $('simulationState').title = controller.status;
  syncScene();
}

async function connectLinks() {
  try {
    const result = await fetch('/api/site', {cache: 'no-store'});
    // The main detector also serves /bus. A missing viewer-specific route means
    // its console links should retain the current host and port.
    if (!result.ok && ![403, 404].includes(result.status)) throw new Error('Service discovery unavailable.');
    const config = result.ok ? await result.json() : {};
    document.querySelectorAll('[data-console-link]').forEach(link => {
      link.href = serviceLink(location.href, config.api_port); link.removeAttribute('aria-disabled');
    });
  } catch {
    document.querySelectorAll('[data-console-link]').forEach(link => link.setAttribute('aria-disabled', 'true'));
  }
}

async function poll() {
  if (stopped || document.hidden || polling) return;
  clearTimeout(pollTimer); polling = true;
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 3500);
  pollAbort = controller;
  try {
    const result = await fetch('/api/bus/state', {cache: 'no-store', signal: controller.signal});
    if (!result.ok) throw new Error(`Connection ${result.status}`);
    snapshot = await result.json(); receivedAt = performance.now(); online = true;
  } catch { online = false; }
  finally {
    clearTimeout(timeout); pollAbort = null; polling = false;
    if (!stopped) updatePanel();
    if (!stopped && !document.hidden) pollTimer = setTimeout(poll, 400);
  }
}

function startScene() {
  const host = $('canvasHost');
  if (reducedMotion) $('renderStatus').textContent = 'Reduced motion · journey state remains live';
  const renderer = new THREE.WebGLRenderer({antialias: true, alpha: false, powerPreference: 'high-performance'});
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.shadowMap.autoUpdate = false; renderer.shadowMap.needsUpdate = true;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.10;
  host.append(renderer.domElement);
  renderer.domElement.setAttribute('role', 'img');
  renderer.domElement.setAttribute('aria-label', 'Detailed autonomous Singapore bus: 6 priority seats, 20 standard seats, two doors, two CCTV cameras and an accessible ramp.');
  const scene = new THREE.Scene(); scene.background = new THREE.Color('#d6ebcc'); scene.fog = new THREE.Fog('#d6ebcc', 27, 90);
  const room = new RoomEnvironment(), pmrem = new THREE.PMREMGenerator(renderer);
  const environment = pmrem.fromScene(room, .035); scene.environment = environment.texture; scene.environmentIntensity = .75;
  room.dispose(); pmrem.dispose();
  scene.add(new THREE.HemisphereLight('#efffe4', '#88a67b', 1.7));
  const sun = new THREE.DirectionalLight('#fff7e7', 3.1); sun.position.set(2.5, 11, 8); sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048); sun.shadow.camera.left = -10; sun.shadow.camera.right = 10;
  sun.shadow.camera.top = 9; sun.shadow.camera.bottom = -9; sun.shadow.camera.near = .5; sun.shadow.camera.far = 35;
  sun.shadow.normalBias = .020; sun.shadow.bias = -.0001; sun.shadow.radius = 3.5; scene.add(sun);
  const rim = new THREE.DirectionalLight('#d9f1cf', 2.4); rim.position.set(-5, 7, -6); scene.add(rim);
  const fill = new THREE.DirectionalLight('#e5f9ea', .8); fill.position.set(6, 3, -2); scene.add(fill);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(200, 200), new THREE.MeshStandardMaterial({color: '#aec99e', roughness: .9, metalness: .02}));
  floor.rotation.x = -Math.PI / 2; floor.position.y = -.027; floor.receiveShadow = true; scene.add(floor);
  const road = buildRoad(scene);
  // A broad studio wash gives the presentation floor a soft horizon.
  const cabinLight = new THREE.PointLight('#fff2d8', 3, 11, 2); cabinLight.position.set(1, 2.35, 0); scene.add(cabinLight);
  model = buildBus(scene);
  // Fog belongs to the studio floor, not to the product being inspected; a
  // farther portrait camera should not wash out the bus paint and glazing.
  model.chassis.traverse(object => {
    if (!object.isMesh) return;
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) material.fog = false;
  });

  const camera = new THREE.PerspectiveCamera(31, 1, .06, 130);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = .075; controls.enablePan = true;
  controls.rotateSpeed = .52; controls.zoomSpeed = .65; controls.panSpeed = .45;
  controls.minDistance = 3.5; controls.maxDistance = 33; controls.minPolarAngle = .025; controls.maxPolarAngle = Math.PI * .488;
  controls.autoRotateSpeed = .30;
  const views = {
    exterior: {eye: [10.4, 6.4, 14.8], target: [0, 1.3, 0], cut: 0, fov: 31},
    cabin: {eye: [8.1, 11.6, 12.5], target: [0, .8, 0], cut: 1, fov: 31},
    top: {eye: [.01, 21, .08], target: [0, .7, 0], cut: 1, fov: 31}
  };
  const state = {view: 'exterior', cut: 0, cutTarget: 0, door: 0, ramp: 0, tween: null, paused: false};
  const travel = createTravelMotion();
  let travelKey = '';
  let previous = null, first = true, contextLost = false, resizePending = false, renderedFrames = 0, shadowUpdates = 0;
  const loop = createRenderLoop(draw);
  syncScene = () => {
    const age = observationAge(performance.now(), receivedAt);
    const target = controllerPresentation(snapshot, age, online);
    const motion = travelPresentation(snapshot, age, online);
    const ledChanged = model.setEntranceDisplay(entranceLedPresentation(snapshot, age, online));
    // Repeated current motion can recover an idle render loop even when its
    // phase key has not changed (for example after a stale or skipped frame).
    if (ledChanged || shouldWakeTravel(motion, travelKey, host.dataset.renderState === 'idle', reducedMotion)
        || (target.doors !== null && target.doors !== state.door)
        || (target.ramp !== null && target.ramp !== state.ramp)) loop.invalidate();
    travelKey = motion.key;
  };
  suspendScene = () => { loop.suspend(); previous = null; };
  resumeScene = () => {
    if (!state.paused && !contextLost && !document.hidden && !stopped) {
      previous = null; if (resizePending) resize(); loop.resume();
    }
  };
  const V = values => new THREE.Vector3(...values);
  function framing(view) {
    const eye = V(view.eye), target = V(view.target), aspect = host.clientWidth / host.clientHeight;
    const narrow = aspect < .85;
    const scale = Math.max(1, (narrow ? 1.3 : 1.62) / aspect);
    eye.sub(target).multiplyScalar(scale).add(target);
    if (narrow) { eye.y += 1; target.y += 1; }
    return {eye, target};
  }
  function goView(name, instant = false) {
    const view = views[name]; if (!view) return;
    const frame = framing(view); state.view = name; state.cutTarget = view.cut;
    // Portrait viewports need a more distant fitted camera. Keep OrbitControls
    // from clamping that position back to the desktop zoom limit.
    controls.maxDistance = Math.max(33, frame.eye.distanceTo(frame.target) * 1.35);
    controls.autoRotate = false; $('rotate').setAttribute('aria-pressed', 'false');
    state.tween = {at: performance.now(), from: camera.position.clone(), to: frame.eye, oldTarget: controls.target.clone(), target: frame.target, duration: reducedMotion || instant ? 0 : 1500};
    document.querySelectorAll('[data-view]').forEach(button => { const selected = button.dataset.view === name; button.classList.toggle('selected', selected); button.setAttribute('aria-pressed', String(selected)); });
    $('seatLabel').hidden = true; model.highlight();
    loop.invalidate();
  }
  document.querySelectorAll('[data-view]').forEach(button => button.onclick = () => goView(button.dataset.view));
  $('rotate').onclick = () => { controls.autoRotate = !controls.autoRotate; state.tween = null; $('rotate').setAttribute('aria-pressed', String(controls.autoRotate)); loop.invalidate(); };
  $('reset').onclick = () => { goView('exterior'); updatePanel(); };
  $('pause3d').onclick = () => {
    state.paused = !state.paused;
    controls.enabled = !state.paused;
    $('pause3d').setAttribute('aria-pressed', String(state.paused));
    $('pause3d').setAttribute('aria-label', state.paused ? 'Resume 3D rendering' : 'Pause 3D rendering');
    $('pause3d').title = state.paused ? 'Resume 3D rendering on this device' : 'Pause 3D rendering; passenger messages keep updating';
    $('pause3d').querySelector('path').setAttribute('d', state.paused ? 'M8 5l11 7-11 7V5Z' : 'M8 5v14M16 5v14');
    $('renderStatus').textContent = state.paused ? '3D paused · messages still update' : reducedMotion ? 'Reduced motion · journey state remains live' : 'Renders on this device';
    $('interactionHint').textContent = state.paused ? 'Open the bus on your other device to continue' : 'Drag to explore · Scroll to zoom';
    document.querySelectorAll('[data-view], #rotate, #reset').forEach(button => { button.disabled = state.paused; });
    if (state.paused) { controls.autoRotate = false; $('rotate').setAttribute('aria-pressed', 'false'); suspendScene(); }
    else resumeScene();
    host.dataset.renderState = state.paused ? 'paused' : 'active';
  };
  controls.addEventListener('change', () => loop.invalidate());
  controls.addEventListener('start', () => { state.tween = null; controls.autoRotate = false; $('rotate').setAttribute('aria-pressed', 'false'); });
  const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2(); let down = null;
  renderer.domElement.addEventListener('pointerdown', event => { down = {x: event.clientX, y: event.clientY}; });
  renderer.domElement.addEventListener('pointerup', event => {
    if (state.paused || !down || Math.hypot(event.clientX - down.x, event.clientY - down.y) > 5 || state.cut < .9) return;
    const bounds = renderer.domElement.getBoundingClientRect(); pointer.set((event.clientX - bounds.left) / bounds.width * 2 - 1, -(event.clientY - bounds.top) / bounds.height * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(model.seats.map(seat => seat.group), true)[0];
    let object = hit?.object; while (object && !object.userData.seat) object = object.parent;
    loop.invalidate();
    if (!object) { model.highlight(); $('seatLabel').hidden = true; return; }
    const seat = object.userData.seat; model.highlight(seat.id); $('seatLabel').hidden = false;
    $('seatNumber').textContent = seat.id; $('seatType').textContent = seat.type === 'priority' ? 'Priority seat · accessible cabin' : 'Standard passenger seat';
  });
  function resize() {
    if (state.paused || document.hidden || stopped || contextLost) { resizePending = true; return; }
    resizePending = false;
    renderer.setPixelRatio(renderPixelRatio(host.clientWidth, host.clientHeight, devicePixelRatio));
    renderer.setSize(host.clientWidth, host.clientHeight, false); camera.aspect = host.clientWidth / host.clientHeight;
    // Reserve breathing room beside the glass panel without shrinking the model.
    const width = host.clientWidth, height = host.clientHeight;
    camera.setViewOffset(width, height, width > 1050 ? width * .09 : width > 640 ? width * .16 : 0, width < 640 ? -height * .15 : 0, width, height);
    camera.updateProjectionMatrix(); goView(state.view, true);
  }
  const resizeObserver = new ResizeObserver(resize); resizeObserver.observe(host); resize();
  function draw(now) {
    if (stopped || document.hidden || state.paused || contextLost) return false;
    const delta = previous === null ? 1 / 60 : Math.min((now - previous) / 1000, .05); previous = now;
    let geometryChanged = false;
    if (state.tween) {
      const tween = state.tween, t = tween.duration ? Math.min(1, (now - tween.at) / tween.duration) : 1;
      const eased = t * t * (3 - 2 * t);
      camera.position.lerpVectors(tween.from, tween.to, eased); controls.target.lerpVectors(tween.oldTarget, tween.target, eased);
      if (t === 1) state.tween = null;
    }
    const step = reducedMotion ? 1 : 1 - Math.exp(-delta * 4.2);
    const nextCut = state.cut + (state.cutTarget - state.cut) * step;
    if (state.cut !== state.cutTarget) { state.cut = Math.abs(nextCut - state.cutTarget) < .001 ? state.cutTarget : nextCut; model.setCutaway(state.cut); geometryChanged = true; }
    // Use the observation clock rather than the earlier rAF frame-start clock.
    const age = observationAge(performance.now(), receivedAt);
    const controller = controllerPresentation(snapshot, age, online);
    const motion = travel.step(travelPresentation(snapshot, age, online), delta, reducedMotion);
    model.setTravel(motion.distance); road.setTravel(motion.distance);
    host.dataset.travelSpeed = motion.speed.toFixed(3);
    host.dataset.travelDistance = motion.distance.toFixed(3);
    // Interpolate only server-provided targets. Offline, unknown and emergency
    // states freeze the current geometry; no local timer completes assistance.
    if (controller.ramp !== null && state.ramp !== controller.ramp) {
      state.ramp += (controller.ramp - state.ramp) * (reducedMotion ? 1 : 1 - Math.exp(-delta * 5));
      if (Math.abs(state.ramp - controller.ramp) < .001) state.ramp = controller.ramp;
      model.setRamp(state.ramp); geometryChanged = true;
    }
    if (controller.doors !== null && state.door !== controller.doors) {
      state.door += (controller.doors - state.door) * step;
      if (Math.abs(state.door - controller.doors) < .001) state.door = controller.doors;
      model.setDoors(state.door); geometryChanged = true;
    }
    // Wheel silhouettes and flat road markings do not need new shadow maps.
    // Camera orbit likewise reuses the shadow; door/ramp/cutaway changes refresh it.
    if (geometryChanged) renderer.shadowMap.needsUpdate = true;
    const cameraChanged = controls.update(delta);
    if (renderer.shadowMap.needsUpdate) shadowUpdates++;
    renderer.render(scene, camera);
    host.dataset.renderedFrames = String(++renderedFrames);
    host.dataset.shadowUpdates = String(shadowUpdates);
    host.dataset.renderState = 'active';
    if (first) { first = false; $('loading').style.opacity = '0'; setTimeout(() => { $('loading').hidden = true; }, reducedMotion ? 0 : 750); }
    const moving = Boolean(motion.active || state.tween || controls.autoRotate || cameraChanged || state.cut !== state.cutTarget
      || (controller.doors !== null && state.door !== controller.doors)
      || (controller.ramp !== null && state.ramp !== controller.ramp));
    if (!moving) { previous = null; host.dataset.renderState = 'idle'; }
    return moving;
  }
  renderer.domElement.addEventListener('webglcontextlost', event => { event.preventDefault(); contextLost = true; suspendScene(); $('loading').hidden = true; $('graphicsError').hidden = false; });
  disposeScene = () => {
    loop.dispose(); controls.dispose(); resizeObserver.disconnect();
    const geometries = new Set(), materials = new Set(), textures = new Set();
    scene.traverse(object => {
      if (object.geometry) geometries.add(object.geometry);
      if (object.material) for (const item of Array.isArray(object.material) ? object.material : [object.material]) materials.add(item);
    });
    for (const item of materials) { for (const value of Object.values(item)) if (value?.isTexture) textures.add(value); item.dispose(); }
    for (const item of geometries) item.dispose();
    for (const item of textures) item.dispose();
    sun.shadow.dispose(); environment.dispose(); renderer.dispose(); renderer.forceContextLoss();
  };
  if (document.hidden) suspendScene(); else loop.invalidate();
}

try { startScene(); } catch (error) { console.error('Bus renderer:', error); $('loading').hidden = true; $('graphicsError').hidden = false; }
poll();
connectLinks();
// Message freshness is independent of the render loop, including while paused.
let panelTimer = setInterval(() => { if (!document.hidden && !stopped) updatePanel(); }, 250);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { clearTimeout(pollTimer); pollAbort?.abort(); suspendScene(); if (voiceEnabled) busSpeaker.cancel(); }
  else if (!stopped) { updatePanel(); poll(); resumeScene(); }
});
window.addEventListener('pagehide', event => {
  stopped = true; clearTimeout(pollTimer); clearInterval(panelTimer); pollAbort?.abort(); suspendScene();
  busSpeaker.dispose();
  if (!event.persisted) { messagePanel.dispose(); disposeScene(); }
});
window.addEventListener('pageshow', event => {
  if (!event.persisted) return;
  stopped = false; online = false; updatePanel(); poll(); resumeScene();
  panelTimer = setInterval(() => { if (!document.hidden && !stopped) updatePanel(); }, 250);
});
