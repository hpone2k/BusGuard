import test from 'node:test';
import assert from 'node:assert/strict';
import {PANEL_STORAGE_KEY, panelBounds, clampPanelRect, defaultPanelState, parsePanelState,
  panelLayout, resizePanelRect, togglePanelMinimized, togglePanelMaximized} from '../static/bus/src/panel-layout.js';
import {mountMessagePanel} from '../static/bus/src/message-panel.js';

const desktop = {width: 1440, height: 900, bottomInset: 100};
function within(rect, viewport) {
  const bounds = panelBounds(viewport);
  assert.ok(rect.x >= bounds.left && rect.y >= bounds.top);
  assert.ok(rect.x + rect.width <= bounds.right + .001);
  assert.ok(rect.y + rect.height <= bounds.bottom + .001);
}

test('desktop and phone defaults leave room for the model and preserve navigation access', () => {
  const normal = defaultPanelState(desktop);
  assert.equal(normal.mode, 'normal'); assert.equal(normal.normal.width, 340);
  assert.equal(defaultPanelState({width: 800, height: 700, bottomInset: 90}).normal.width, 300);
  const phone = {width: 390, height: 844, bottomInset: 95}, compact = defaultPanelState(phone);
  assert.equal(compact.mode, 'minimized'); assert.equal(panelLayout(compact, phone).height, 256);
  within(panelLayout(normal, desktop), desktop); within(panelLayout(compact, phone), phone);
});

test('maximize and minimize restore the original custom rectangle without overwriting it', () => {
  const state = {...defaultPanelState(desktop), normal: {x: 100, y: 80, width: 390, height: 520}};
  const maximized = togglePanelMaximized(state);
  assert.equal(maximized.mode, 'maximized'); assert.deepEqual(maximized.normal, state.normal);
  assert.equal(panelLayout(maximized, desktop).width, 580);
  const compact = togglePanelMinimized(maximized);
  assert.equal(compact.mode, 'minimized'); assert.equal(compact.restoreMode, 'maximized');
  assert.equal(togglePanelMinimized(compact).mode, 'maximized');
  assert.deepEqual(togglePanelMaximized(maximized), state);
});

test('corrupted storage is rejected and valid customization is copied without extra fields', () => {
  for (const raw of ['{broken', null, {version: 1, mode: 'normal', normal: {x: 0, y: 0, width: NaN, height: 500}},
    {version: 1, mode: 'hidden', normal: {x: 0, y: 0, width: 300, height: 500}},
    {version: 1, mode: 'normal', normal: {x: 0, y: 0, width: 1e9, height: 500}}])
    assert.deepEqual(parsePanelState(raw, desktop), defaultPanelState(desktop));
  const state = {...defaultPanelState(desktop), mode: 'maximized'};
  assert.deepEqual(parsePanelState(JSON.stringify({...state, private: 'discard'}), desktop), state);
});

test('every mode stays inside a resized viewport above the view controls', () => {
  const state = {...defaultPanelState(desktop), normal: {x: 1300, y: 700, width: 650, height: 750}};
  for (const viewport of [{width: 320, height: 568, bottomInset: 98}, {width: 568, height: 320, bottomInset: 90},
    {width: 850, height: 500, bottomInset: 110}, desktop]) {
    for (const mode of ['normal', 'minimized', 'maximized']) within(panelLayout({...state, mode}, viewport), viewport);
  }
});

test('resize retains its top-left corner and cannot stretch into navigation or outside the viewport', () => {
  const rect = {x: 300, y: 80, width: 340, height: 400};
  const larger = resizePanelRect(rect, 10000, 10000, desktop);
  assert.equal(larger.x, rect.x); assert.equal(larger.y, rect.y); within(larger, desktop);
  const smaller = resizePanelRect(rect, -10000, -10000, desktop);
  assert.equal(smaller.width, 280); assert.equal(smaller.height, 300);
  within(clampPanelRect({x: -200, y: -90, width: 300, height: 400}, panelBounds(desktop)), desktop);
});

class Element extends EventTarget {
  constructor(name = 'div') { super(); this.name = name; this.style = {}; this.dataset = {}; this.attributes = new Map();
    this.classes = new Set(); this.classList = {add: value => this.classes.add(value), remove: value => this.classes.delete(value)}; }
  setAttribute(key, value) {this.attributes.set(key, value);}
  closest(selector) {return selector.split(', ').includes(this.name) ? this : null;}
  setPointerCapture(id) {this.captured = id;}
  releasePointerCapture() {this.captured = null;}
  getBoundingClientRect() {return {top: 620, bottom: 675};}
}
function event(type, values = {}) {
  const result = new Event(type, {cancelable: true});
  for (const [key, value] of Object.entries(values)) Object.defineProperty(result, key, {value});
  return result;
}
function fixture(t, saved = null) {
  const old = {};
  for (const key of ['window', 'document', 'localStorage', 'requestAnimationFrame', 'cancelAnimationFrame']) old[key] = globalThis[key];
  const win = new Element(); win.innerWidth = 800; win.innerHeight = 700;
  const document = {documentElement: {clientWidth: 800, clientHeight: 700}};
  const storage = new Map(saved ? [[PANEL_STORAGE_KEY, JSON.stringify(saved)]] : []);
  Object.assign(globalThis, {window: win, document,
    localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
    requestAnimationFrame: callback => setTimeout(callback, 0), cancelAnimationFrame: clearTimeout});
  const panel = new Element(), viewport = new Element(), controls = new Element();
  const parts = Object.fromEntries(['panelDragHandle', 'panelDetails', 'panelMinimize', 'panelMaximize', 'panelReset', 'panelResize', 'panelPositionStatus']
    .map(id => [id, new Element(['panelMinimize', 'panelMaximize', 'panelReset', 'panelResize'].includes(id) ? 'button' : 'div')]));
  parts.footer = new Element();
  panel.querySelector = selector => selector === '.panel-footer' ? parts.footer : parts[selector.slice(1)];
  const instance = mountMessagePanel({panel, viewport, controls});
  t.after(() => {instance.dispose(); for (const [key, value] of Object.entries(old)) {
    if (value === undefined) delete globalThis[key]; else globalThis[key] = value;
  }});
  return {panel, parts, instance, win, storage, document};
}

test('panel controls keep the live summary mounted, toggle details, and persist layout', t => {
  const {panel, parts, instance, storage} = fixture(t);
  const initial = instance.getState();
  parts.panelMinimize.dispatchEvent(event('click'));
  assert.equal(panel.dataset.panelMode, 'minimized'); assert.equal(parts.panelDetails.hidden, true);
  assert.equal(parts.panelMinimize.attributes.get('aria-expanded'), 'false');
  assert.equal(parts.panelResize.hidden, true);
  parts.panelMinimize.dispatchEvent(event('click'));
  assert.equal(parts.panelDetails.hidden, false);
  parts.panelMaximize.dispatchEvent(event('click'));
  assert.equal(panel.dataset.panelMode, 'maximized');
  assert.equal(parts.panelDragHandle.attributes.has('aria-disabled'), false);
  assert.equal(parts.panelDragHandle.dataset.dragDisabled, 'true');
  for (const button of [parts.panelMinimize, parts.panelMaximize, parts.panelReset])
    assert.notEqual(button.disabled, true);
  parts.panelMaximize.dispatchEvent(event('click'));
  assert.deepEqual(instance.getState(), initial);
  assert.deepEqual(JSON.parse(storage.get(PANEL_STORAGE_KEY)), initial);
});

test('drag and keyboard movement clamp to bounds; Escape restores pre-drag geometry', t => {
  const {panel, parts, instance, win} = fixture(t);
  const before = instance.getState();
  parts.panelDragHandle.dispatchEvent(event('pointerdown', {button: 0, pointerId: 1, clientX: 600, clientY: 50}));
  assert.equal(parts.panelDragHandle.captured, 1);
  win.dispatchEvent(event('pointermove', {pointerId: 1, clientX: -400, clientY: 900}));
  assert.equal(parseInt(panel.style.left), 12);
  within(panelLayout(instance.getState(), {width: 800, height: 700, bottomInset: 80}), {width: 800, height: 700, bottomInset: 80});
  win.dispatchEvent(event('keydown', {key: 'Escape'}));
  assert.deepEqual(instance.getState(), before);
  parts.panelDragHandle.dispatchEvent(event('keydown', {key: 'ArrowLeft', shiftKey: true}));
  assert.equal(instance.getState().normal.x, before.normal.x - 1);
  parts.panelDragHandle.dispatchEvent(event('keydown', {key: 'Home'}));
  assert.deepEqual(instance.getState(), before);
});

test('keyboard resizing changes size and disposed controls cannot change state', t => {
  const {parts, instance} = fixture(t);
  const initial = instance.getState();
  parts.panelResize.dispatchEvent(event('keydown', {key: 'ArrowDown', shiftKey: false}));
  assert.equal(instance.getState().normal.height, initial.normal.height + 10);
  instance.dispose(); const frozen = instance.getState();
  parts.panelMinimize.dispatchEvent(event('click'));
  assert.deepEqual(instance.getState(), frozen);
});
