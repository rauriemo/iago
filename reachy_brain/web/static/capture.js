// Browser archive sampling only; these counters do not measure physical sensor drops.
export async function captureFrame(source, sources, api, notice, clock = null) {
  if (sources.get(source.id) !== source) return;
  if (!source.captureStats || source.captureStats.generation !== source.generation) {
    source.captureStats = {generation: source.generation, attempts: 0, accepted: 0, busy: 0, unavailable: 0, failed: 0};
  }
  const stats = source.captureStats;
  const valid = () => sources.get(source.id) === source && source.generation === stats.generation;
  const render = () => {
    if (valid() && source.captureStatus) source.captureStatus.textContent =
      `Browser sampling: ${stats.accepted}/${stats.attempts} uploads accepted; ${stats.busy} busy skips; ${stats.unavailable} unavailable frames; ${stats.failed} failed attempts.`;
  };
  if (source.busy) { stats.busy++; render(); return; }
  if (!source.video.videoWidth || !source.video.videoHeight) { stats.unavailable++; render(); return; }
  stats.attempts++;
  source.busy = true;
  const controller = new AbortController();
  source.captureAbort = controller;
  let timedOut = false;
  const deadline = setTimeout(() => { timedOut = true; controller.abort(); }, 10000);
  try {
    if (clock && (!clock.map() || clock.now() - clock.sample.received > 5)) await clock.refresh(api);
    if (!valid() || controller.signal.aborted) return;
    const clockGeneration = clock?.generation;
    const video = source.video, canvas = source.canvas;
    canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    const observed = clock ? clock.now() : null;
    const mapped = clock ? clock.map(observed) : null;
    if (clock && !mapped) throw Error('Capture clock unavailable; waiting for synchronization.');
    const captured = mapped ? mapped.time : Date.now()/1000;
    const headers = {'X-Captured-At': String(captured), 'X-Frame-Sequence': String(stats.attempts)};
    if (mapped) Object.assign(headers, {'X-Clock-Owner': mapped.owner,
      'X-Source-Monotonic': String(observed), 'X-Clock-Uncertainty': String(mapped.uncertainty)});
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .92));
    if (!valid() || (clock && clock.generation !== clockGeneration)) return;
    controller.signal.throwIfAborted();
    if (!blob) { stats.failed++; return; }
    const response = await api(`/api/frame/${source.id}/${stats.generation}`, {
      method: 'POST', headers, body: blob, signal: controller.signal
    });
    const frame = await response.json();
    if (!valid() || controller.signal.aborted) return;
    stats.accepted++;
    return frame;
  } catch (error) {
    if (valid()) {
      stats.failed++;
      if (timedOut) notice('Camera or screen upload timed out.');
      else if (!controller.signal.aborted) notice(error.message);
    }
  } finally {
    clearTimeout(deadline);
    if (source.captureAbort === controller) source.captureAbort = null;
    source.busy = false;
    render();
  }
}
