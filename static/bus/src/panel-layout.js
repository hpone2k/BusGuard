export const PANEL_STORAGE_KEY = 'bustech.bus.message-panel.v1';
const finite = (value, fallback) => Number.isFinite(value) ? value : fallback;
const clamp = (value, low, high) => Math.min(Math.max(value, low), Math.max(low, high));

export function panelBounds(viewport = {}) {
  const width = Math.max(1, finite(viewport.width, 1024));
  const height = Math.max(1, finite(viewport.height, 768));
  const gutter = Math.min(12, width / 4, height / 4);
  const bottom = Math.max(gutter + 1, height - Math.max(0, finite(viewport.bottomInset, 0)) - gutter);
  return {left: gutter, top: gutter, right: width - gutter, bottom,
    width: Math.max(1, width - gutter * 2), height: Math.max(1, bottom - gutter)};
}

export function clampPanelRect(rect, bounds, {minWidth = 280, minHeight = 300} = {}) {
  const width = clamp(finite(rect?.width, 340), Math.min(minWidth, bounds.width), bounds.width);
  const height = clamp(finite(rect?.height, 480), Math.min(minHeight, bounds.height), bounds.height);
  return {x: clamp(finite(rect?.x, bounds.right - width), bounds.left, bounds.right - width),
    y: clamp(finite(rect?.y, bounds.top), bounds.top, bounds.bottom - height), width, height};
}

export function defaultPanelState(viewport) {
  const bounds = panelBounds(viewport), mobile = viewport.width <= 640;
  const tablet = viewport.width <= 1050;
  const width = Math.min(mobile ? 304 : tablet ? 300 : 340, bounds.width);
  return {version: 1, mode: mobile ? 'minimized' : 'normal', restoreMode: 'normal', normal: clampPanelRect({
    x: bounds.right - width - (mobile ? 0 : 16), y: mobile ? 76 : 28,
    width, height: mobile ? 440 : tablet ? 450 : 490,
  }, bounds)};
}

export function parsePanelState(raw, viewport) {
  try { if (typeof raw === 'string') raw = JSON.parse(raw); } catch { return defaultPanelState(viewport); }
  const rect = raw?.normal;
  if (raw?.version !== 1 || !['normal', 'minimized', 'maximized'].includes(raw.mode)
      || !rect || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(rect[key]))
      || Math.abs(rect.x) > 100000 || Math.abs(rect.y) > 100000
      || rect.width < 160 || rect.height < 140 || rect.width > 4000 || rect.height > 4000)
    return defaultPanelState(viewport);
  // Preserve the normal rectangle when maximizing or minimizing; each visible
  // layout is clamped separately, including after orientation changes.
  return {version: 1, mode: raw.mode, restoreMode: raw.restoreMode === 'maximized' ? 'maximized' : 'normal',
    normal: {x: rect.x, y: rect.y, width: rect.width, height: rect.height}};
}

export function panelLayout(state, viewport) {
  const bounds = panelBounds(viewport);
  if (state.mode === 'maximized') {
    const width = Math.min(580, bounds.width), height = Math.min(720, bounds.height);
    return clampPanelRect({x: bounds.left + (bounds.width - width) / 2,
      y: bounds.top + (bounds.height - height) / 2, width, height}, bounds);
  }
  if (state.mode === 'minimized')
    return clampPanelRect({...state.normal, height: 256}, bounds, {minHeight: 170});
  return clampPanelRect(state.normal, bounds);
}

export function resizePanelRect(rect, dx, dy, viewport) {
  const bounds = panelBounds(viewport), current = clampPanelRect(rect, bounds);
  return {...current,
    width: clamp(current.width + finite(dx, 0), Math.min(280, bounds.right - current.x), bounds.right - current.x),
    height: clamp(current.height + finite(dy, 0), Math.min(300, bounds.bottom - current.y), bounds.bottom - current.y)};
}

export function togglePanelMinimized(state) {
  return state.mode === 'minimized' ? {...state, mode: state.restoreMode || 'normal'}
    : {...state, restoreMode: state.mode, mode: 'minimized'};
}

export function togglePanelMaximized(state) {
  return {...state, mode: state.mode === 'maximized' ? 'normal' : 'maximized', restoreMode: 'normal'};
}
