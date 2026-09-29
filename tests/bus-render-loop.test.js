import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRenderLoop, renderPixelRatio} from '../static/bus/src/render-loop.js';

function frames(draw) {
  let id = 0, now = 0;
  const queue = new Map();
  const loop = createRenderLoop(draw, {
    request(callback) { queue.set(++id, callback); return id; },
    cancel(key) { queue.delete(key); },
  });
  return {loop, queue, tick() {
    const batch = [...queue.values()]; queue.clear();
    now += 1000 / 60; batch.forEach(callback => callback(now));
  }};
}

test('unchanged data and a still camera consume no animation frames', () => {
  let rendered = 0;
  const {loop, queue, tick} = frames(() => { rendered++; return false; });
  assert.equal(queue.size, 0);
  loop.invalidate(); loop.invalidate(); loop.invalidate();
  assert.equal(queue.size, 1, 'Coalesce input and state arriving before the same frame');
  tick();
  assert.equal(rendered, 1); assert.equal(queue.size, 0);
  for (let i = 0; i < 120; i++) tick();
  assert.equal(rendered, 1, 'A stationary scene must stay idle until invalidated');
  loop.invalidate(); tick();
  assert.equal(rendered, 2, 'A later controller update can wake an idle scene');
});

test('animations and damping finish, then stop scheduling frames', () => {
  let remaining = 8;
  const {loop, queue, tick} = frames(() => --remaining > 0);
  loop.invalidate();
  for (let i = 0; i < 8; i++) tick();
  assert.equal(remaining, 0); assert.equal(queue.size, 0);
});

test('controls change raised during a draw is retained without duplicate loops', () => {
  let count = 0, loop;
  const harness = frames(() => { if (++count === 1) loop.invalidate(); return false; });
  loop = harness.loop; loop.invalidate(); harness.tick();
  assert.equal(harness.queue.size, 1);
  harness.tick(); assert.equal(count, 2); assert.equal(harness.queue.size, 0);
});

test('pause/hidden cancels pending work; updates do not render until resume', () => {
  let count = 0;
  const {loop, queue, tick} = frames(() => { count++; return true; });
  loop.invalidate(); tick(); assert.equal(count, 1);
  loop.suspend(); assert.equal(queue.size, 0);
  loop.invalidate(); loop.invalidate(); tick(); assert.equal(count, 1);
  loop.resume(); loop.resume(); assert.equal(queue.size, 1);
  tick(); assert.equal(count, 2);
  loop.dispose(); loop.resume(); loop.invalidate(); tick();
  assert.equal(count, 2); assert.equal(queue.size, 0);
});

test('retina and 4K viewports stay within the two-million-pixel render budget', () => {
  for (const [width, height, dpr] of [[390, 844, 3], [1920, 1080, 2], [3840, 2160, 2], [7680, 4320, 1]]) {
    const ratio = renderPixelRatio(width, height, dpr);
    assert(ratio > 0 && ratio <= 1.5 && ratio <= dpr);
    assert(width * height * ratio * ratio <= 2_000_001);
  }
  assert.equal(renderPixelRatio(800, 600, 1), 1, 'Standard screens are not needlessly downscaled');
  assert(Number.isFinite(renderPixelRatio(0, 0, 0)), 'Collapsed layout is safe during resize');
});
