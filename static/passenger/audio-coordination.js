// One audio turn per page. A turn stays owned until its caller confirms that
// playback has ended or explicitly stops it; generating a response is not enough.
export class AudioTurnCoordinator {
  constructor() { this.current = null; this.queue = []; this.listeners = new Set(); }

  get state() {
    return {kind: this.current?.kind || null, waiting: this.queue.length,
      announcementPending: this.current?.kind === 'announcement'
        || this.queue.some(turn => turn.kind === 'announcement')};
  }

  subscribe(listener) {
    this.listeners.add(listener);
    try { listener(this.state); } catch { /* A display callback cannot break the audio lock. */ }
    return () => this.listeners.delete(listener);
  }

  notify() {
    const state = this.state;
    for (const listener of this.listeners) {
      try { listener(state); } catch { /* Audio ownership survives a failed observer. */ }
    }
  }

  acquire({kind, owner, signal} = {}) {
    if (!['announcement', 'realtime'].includes(kind)) return Promise.reject(new TypeError('Unknown audio turn.'));
    if (signal?.aborted) return Promise.reject(new DOMException('Audio turn cancelled.', 'AbortError'));
    return new Promise((resolve, reject) => {
      const turn = {kind, owner, signal, resolve, reject};
      turn.cancel = () => {
        const index = this.queue.indexOf(turn);
        if (index < 0) return;
        this.queue.splice(index, 1); signal?.removeEventListener('abort', turn.cancel);
        reject(new DOMException('Audio turn cancelled.', 'AbortError'));
        this.notify(); this.advance();
      };
      signal?.addEventListener('abort', turn.cancel, {once: true});
      this.queue.push(turn); this.notify(); this.advance();
    });
  }

  advance() {
    if (this.current || !this.queue.length) return;
    const turn = this.queue.shift();
    turn.signal?.removeEventListener('abort', turn.cancel);
    this.current = turn;
    let released = false;
    this.notify();
    turn.resolve({release: () => {
      if (released || this.current !== turn) return;
      released = true; this.current = null;
      // Grant the next queued turn synchronously, without an artificial idle gap.
      if (this.queue.length) this.advance(); else this.notify();
    }});
  }
}

export const audioTurns = new AudioTurnCoordinator();
