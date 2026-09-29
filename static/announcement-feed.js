// Polling can span several controller events. Deliver each fresh event once,
// in order, without replaying an old stop when sound is enabled or reconnected.
export class AnnouncementFeed {
  constructor() { this.reset(); }
  reset() { this.seen = new Set(); this.primed = false; }
  take(state, online = true) {
    if (!online || !state) { this.reset(); return []; }
    const now = state.server_time_ms;
    const history = (state.scenario?.announcement_events || []).filter(event =>
      typeof event?.id === 'string' && typeof event.message === 'string' && event.message.trim()
      && Number.isFinite(now) && Number.isFinite(event.at_ms) && now >= event.at_ms && now - event.at_ms <= 30000);
    const message = state.announcements?.[0] || state.readiness?.message;
    const latest = state.scenario?.announcement;
    const current = typeof message === 'string' && message.trim() ? {
      id: latest?.message === message ? latest.id : `status:${message}`, message,
    } : null;
    const held = state.vehicle?.emergency || state.vehicle?.obstruction || state.vehicle?.revalidation_required;
    const events = this.primed && !held ? history.filter(event => !this.seen.has(event.id)) : [];
    if (current && !this.seen.has(current.id) && !events.some(event => event.id === current.id)) events.push(current);
    for (const event of [...history, ...(current ? [current] : [])]) this.seen.add(event.id);
    while (this.seen.size > 128) this.seen.delete(this.seen.values().next().value);
    this.primed = true;
    return events;
  }
}
