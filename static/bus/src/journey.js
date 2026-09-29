// Presentation motion only. The departure interlock is the sole permission input.
export class VirtualJourney {
  constructor() { this.reset(); }
  reset() { this.speed = 0; this.distance = 0; }
  step(canDepart, elapsedSeconds) {
    const dt = Number.isFinite(elapsedSeconds) ? Math.min(.1, Math.max(0, elapsedSeconds)) : 0;
    // A lost permission stops the virtual scene immediately; no coast-through.
    this.speed = canDepart === true ? Math.min(8 / 3.6, this.speed + .75 * dt) : 0;
    this.distance += this.speed * dt;
    return {speedKph: this.speed * 3.6, distance: this.distance, moving: this.speed > 0};
  }
}
