// Maps browser monotonic observation times to the controller wall clock.
// Round-trip uncertainty is not a physical sensor exposure bound.
export class BrowserClock {
  constructor(now = () => performance.now() / 1000) {
    this.now = now;
    this.generation = 0;
    this.reset();
  }
  reset() {
    this.generation++;
    this.sample = null;
    this.pending?.controller.abort();
    this.pending = null;
  }
  map(at = this.now()) {
    const now = this.now(), sample = this.sample;
    if (!sample || !Number.isFinite(at) || !Number.isFinite(now) || now < sample.received || now - sample.received > 10) {
      if (sample && now < sample.received) this.reset();
      return null;
    }
    return {time: at + sample.offset,
      uncertainty: sample.uncertainty + Math.abs(at - sample.received) * .001,
      owner: sample.owner};
  }
  async refresh(api) {
    if (this.pending) return this.pending.promise;
    const generation = this.generation, controller = new AbortController();
    const pending = {controller};
    this.pending = pending;
    pending.promise = (async () => {
      const deadline = setTimeout(() => controller.abort(), 3000);
      try {
        const sent = this.now();
        const response = await api('/api/clock', {cache: 'no-store', signal: controller.signal});
        const sample = await response.json(), received = this.now();
        if (generation !== this.generation || controller.signal.aborted) return false;
        if (!Number.isFinite(sent) || !Number.isFinite(received) || received < sent ||
            typeof sample.time !== 'number' || !Number.isFinite(sample.time) ||
            typeof sample.owner !== 'string' || !/^[0-9a-f]{32}$/.test(sample.owner)) {
          this.sample = null;
          throw Error('Invalid controller clock sample');
        }
        this.sample = {received, offset: sample.time - (sent + received) / 2,
          uncertainty: (received - sent) / 2, owner: sample.owner};
        return true;
      } finally {
        clearTimeout(deadline);
        if (this.pending === pending) this.pending = null;
      }
    })();
    return pending.promise;
  }
}


// AudioContext times describe processed samples, not calibrated microphone exposure.
export function audioTiming(clock, context, start, end = start) {
  if (context.state !== 'running' || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end < start) return null;
  const current = context.currentTime, observed = clock.now();
  if (!Number.isFinite(current) || end > current + .1) return null;
  const first = clock.map(observed - (current - start));
  const last = clock.map(observed - (current - end));
  if (!first || !last || first.owner !== last.owner) return null;
  return {capture_start:first.time,capture_end:last.time,
    clock_uncertainty:Math.max(first.uncertainty,last.uncertainty)};
}
