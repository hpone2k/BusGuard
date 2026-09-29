// A still scene needs no animation callback and no GPU redraw. Input, a new
// controller target, or a resize wakes it; draw returns true while motion remains.
export function createRenderLoop(draw, {
  request = callback => requestAnimationFrame(callback),
  cancel = id => cancelAnimationFrame(id),
} = {}) {
  let pending = null, active = true, disposed = false, drawing = false, dirty = false;
  function schedule() {
    if (active && !disposed && !drawing && dirty && pending === null) pending = request(frame);
  }
  function frame(now) {
    pending = null;
    if (!active || disposed) return;
    dirty = false; drawing = true;
    try { dirty = Boolean(draw(now)) || dirty; }
    finally { drawing = false; }
    schedule();
  }
  function invalidate() { dirty = true; schedule(); }
  function suspend() {
    active = false;
    if (pending !== null) cancel(pending);
    pending = null;
  }
  return {
    invalidate, suspend,
    resume() { if (!disposed) { active = true; invalidate(); } },
    dispose() { suspend(); disposed = true; },
  };
}

// Keep retina/4K screens from multiplying fill cost without discarding geometry.
export function renderPixelRatio(width, height, deviceRatio = 1) {
  const pixels = Math.max(1, width) * Math.max(1, height);
  return Math.min(Math.max(.25, deviceRatio || 1), 1.5, Math.sqrt(2_000_000 / pixels));
}
