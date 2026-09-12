// Delivery observations only; socket.send success is not a backend acknowledgment.
const owner = crypto.randomUUID();
const started = performance.now();
const counts = Object.fromEntries(['speech_start', 'stop'].map(kind =>
  [kind, {attempts:0, sent:0, unavailable:0, failed:0}]));

export function sendControl(socket, message) {
  const tracked = Object.hasOwn(counts, message.type) ? counts[message.type] : null;
  if (tracked) tracked.attempts++;
  if (socket?.readyState !== 1) {
    if (tracked) tracked.unavailable++;
    return;
  }
  try {
    socket.send(JSON.stringify(message));
    if (tracked) tracked.sent++;
  } catch (error) {
    if (tracked) tracked.failed++;
    throw error;
  }
}

export function controlDeliverySnapshot() {
  return {owner, elapsed_seconds:(performance.now()-started)/1000,
    counts:structuredClone(counts),
    scope:'Page-lifetime speech-start and stop send attempts. Stop includes manual and local guard stops. No payload retained. Sent means socket.send returned, not server receipt; worklet events before page delivery are outside coverage.'};
}
