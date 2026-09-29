import {PANEL_STORAGE_KEY, panelBounds, clampPanelRect, defaultPanelState, parsePanelState,
  panelLayout, resizePanelRect, togglePanelMinimized, togglePanelMaximized} from './panel-layout.js';

export function mountMessagePanel({panel = document.getElementById('messagePanel'),
  viewport = document.querySelector('.showcase'), controls = document.querySelector('.view-controls')} = {}) {
  if (!panel || !viewport) return {reset() {}, dispose() {}, getState: () => null};
  const header = panel.querySelector('#panelDragHandle'), details = panel.querySelector('#panelDetails');
  const footer = panel.querySelector('.panel-footer'), minimize = panel.querySelector('#panelMinimize');
  const maximize = panel.querySelector('#panelMaximize'), resetButton = panel.querySelector('#panelReset');
  const resizeHandle = panel.querySelector('#panelResize'), positionStatus = panel.querySelector('#panelPositionStatus');
  if (![header, details, footer, minimize, maximize, resetButton, resizeHandle, positionStatus].every(Boolean))
    return {reset() {}, dispose() {}, getState: () => null};

  const listeners = [];
  let gesture = null, frame = null, disposed = false;
  const measure = () => {
    const width = document.documentElement.clientWidth || window.innerWidth;
    const height = window.innerHeight || document.documentElement.clientHeight;
    const nav = controls?.getBoundingClientRect();
    // Keep the view/navigation controls reachable, even on a short phone.
    const bottomInset = nav && nav.top > 0 && nav.top < height ? height - nav.top : 80;
    return {width, height, bottomInset};
  };
  let saved = null;
  try { saved = localStorage.getItem(PANEL_STORAGE_KEY); } catch { /* Browser-local customization is optional. */ }
  let state = parsePanelState(saved, measure());
  const on = (target, type, listener, options) => {
    target.addEventListener(type, listener, options);
    listeners.push(() => target.removeEventListener(type, listener, options));
  };
  const persist = () => {
    try { localStorage.setItem(PANEL_STORAGE_KEY, JSON.stringify(state)); } catch { /* The current layout still works. */ }
  };
  function announce(message) { positionStatus.textContent = message; }
  function apply() {
    const rect = panelLayout(state, measure());
    for (const [key, value] of Object.entries({left: rect.x, top: rect.y, width: rect.width, height: rect.height}))
      panel.style[key] = `${Math.round(value)}px`;
    panel.dataset.panelMode = state.mode;
    const compact = state.mode === 'minimized', expanded = state.mode === 'maximized';
    details.hidden = compact; footer.hidden = compact;
    minimize.setAttribute('aria-expanded', String(!compact));
    minimize.setAttribute('aria-label', compact ? 'Restore On board panel' : 'Minimize On board panel');
    minimize.title = compact ? 'Restore panel' : 'Minimize panel';
    maximize.setAttribute('aria-pressed', String(expanded));
    maximize.setAttribute('aria-label', expanded ? 'Restore panel size and position' : 'Maximize On board panel');
    maximize.title = expanded ? 'Restore panel size' : 'Maximize panel';
    resizeHandle.hidden = state.mode !== 'normal';
    // aria-disabled on this group would also disable the nested Restore/Reset
    // buttons for assistive technology. Only dragging is unavailable here.
    header.dataset.dragDisabled = String(expanded);
    header.title = expanded ? 'Restore the panel to move it' : 'Drag to move · Arrow keys move · Home resets';
    return rect;
  }
  function reset() {
    endGesture(false);
    state = defaultPanelState(measure()); apply(); persist();
    announce('Panel position and size reset.');
  }
  function endGesture(cancel = false) {
    if (!gesture) return;
    const active = gesture; gesture = null;
    if (cancel) state = active.before;
    try { active.capture.releasePointerCapture(active.id); } catch { /* Capture can already be lost. */ }
    panel.classList.remove('is-adjusting'); apply(); persist();
    announce(cancel ? 'Panel adjustment cancelled.' : active.kind === 'move' ? 'Panel moved.' : 'Panel resized.');
  }
  function begin(event, kind) {
    if (gesture || event.button !== 0 || state.mode === 'maximized' || kind === 'resize' && state.mode !== 'normal') return;
    if (kind === 'move' && event.target.closest('button, a, input, select, summary')) return;
    event.preventDefault(); event.stopPropagation();
    const rect = apply();
    gesture = {kind, id: event.pointerId, x: event.clientX, y: event.clientY, rect,
      capture: event.currentTarget, before: {...state, normal: {...state.normal}}};
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* Window listeners still track this pointer. */ }
    panel.classList.add('is-adjusting');
  }
  function move(event) {
    if (!gesture || event.pointerId !== gesture.id) return;
    event.preventDefault(); event.stopPropagation();
    const dx = event.clientX - gesture.x, dy = event.clientY - gesture.y;
    if (gesture.kind === 'resize') state.normal = resizePanelRect(gesture.rect, dx, dy, measure());
    else {
      const rect = clampPanelRect({...gesture.rect, x: gesture.rect.x + dx, y: gesture.rect.y + dy},
        panelBounds(measure()), {minHeight: state.mode === 'minimized' ? 170 : 300});
      state.normal = {...state.normal, x: rect.x, y: rect.y, width: rect.width};
    }
    apply();
  }
  function keyboard(event, kind) {
    if (event.target !== event.currentTarget) return;
    if (event.key === 'Home') { event.preventDefault(); reset(); return; }
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)
        || state.mode === 'maximized') return;
    event.preventDefault(); event.stopPropagation();
    const step = event.shiftKey ? 1 : 10;
    const dx = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
    const dy = event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
    const rect = apply();
    if (kind === 'resize') state.normal = resizePanelRect(rect, dx, dy, measure());
    else {
      const moved = clampPanelRect({...rect, x: rect.x + dx, y: rect.y + dy}, panelBounds(measure()),
        {minHeight: state.mode === 'minimized' ? 170 : 300});
      state.normal = {...state.normal, x: moved.x, y: moved.y, width: moved.width};
    }
    apply(); persist(); announce(kind === 'resize' ? 'Panel resized.' : 'Panel moved.');
  }
  on(header, 'pointerdown', event => begin(event, 'move'));
  on(resizeHandle, 'pointerdown', event => begin(event, 'resize'));
  on(window, 'pointermove', move, {passive: false});
  on(window, 'pointerup', event => { if (gesture?.id === event.pointerId) { event.stopPropagation(); endGesture(); } });
  on(window, 'pointercancel', event => { if (gesture?.id === event.pointerId) endGesture(true); });
  on(header, 'lostpointercapture', () => endGesture());
  on(resizeHandle, 'lostpointercapture', () => endGesture());
  on(header, 'keydown', event => keyboard(event, 'move'));
  on(resizeHandle, 'keydown', event => keyboard(event, 'resize'));
  on(window, 'keydown', event => { if (gesture && event.key === 'Escape') { event.preventDefault(); endGesture(true); } });
  on(minimize, 'click', () => {
    endGesture(); state = togglePanelMinimized(state); apply(); persist();
    announce(state.mode === 'minimized' ? 'Panel minimized. Live journey, seats and messages remain visible.' : 'Panel restored.');
  });
  on(maximize, 'click', () => {
    endGesture(); state = togglePanelMaximized(state); apply(); persist();
    announce(state.mode === 'maximized' ? 'Panel maximized.' : 'Original panel size and position restored.');
  });
  on(resetButton, 'click', reset);
  for (const type of ['pointerdown', 'click', 'dblclick', 'wheel'])
    on(panel, type, event => event.stopPropagation(), {passive: true});
  const resize = () => {
    if (frame !== null || disposed) return;
    frame = requestAnimationFrame(() => { frame = null; if (!disposed) apply(); });
  };
  on(window, 'resize', resize);
  on(window, 'pagehide', persist);
  const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(resize) : null;
  if (observer) { observer.observe(viewport); if (controls) observer.observe(controls); }
  apply();
  return {reset, getState: () => ({...state, normal: {...state.normal}}), dispose() {
    if (disposed) return;
    disposed = true; endGesture(); persist();
    if (frame !== null) cancelAnimationFrame(frame);
    observer?.disconnect(); listeners.splice(0).forEach(remove => remove());
  }};
}
