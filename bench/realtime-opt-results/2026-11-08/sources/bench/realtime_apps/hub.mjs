// Shared benchmark application for Next Route Handlers and Astro endpoints.
// In-process bounded replay, publish-triggered delivery, no external service.
const key = Symbol.for('neokit.realtime.parity.v1');
const bus = globalThis[key] ||= {epoch: String(Date.now()), sequence: 0, history: [], subscribers: new Set()};
const encoder = new TextEncoder();
const json = (value, status = 200) => Response.json(value, {status, headers: {'Cache-Control': 'no-store'}});
const frame = (event, value, id = '') => encoder.encode(`${id ? `id: ${id}\n` : ''}event: ${event}\ndata: ${value}\n\n`);

export async function publish(request) {
  const body = await request.text();
  try { if (encoder.encode(body).length > 4096) throw Error(); JSON.parse(body); }
  catch { return new Response('Invalid event', {status: 400}); }
  const sequence = ++bus.sequence;
  bus.history.push({sequence, bytes: frame('message', body, `${bus.epoch}.${sequence}`)});
  if (bus.history.length > 128) bus.history.shift();
  for (const subscriber of bus.subscribers) subscriber.wake();
  return json({sequence});
}

export function stats() { return json({subscribers: bus.subscribers.size}); }

export function feed(request) {
  if (request.headers.get('cookie') !== 'session=allowed') return new Response('Forbidden', {status: 403});
  const cursor = request.headers.get('last-event-id') || new URL(request.url).searchParams.get('after') || '';
  if (cursor.length > 80 || cursor && !/^\d+\.\d+$/.test(cursor)) return new Response('Invalid event cursor', {status: 400});
  const [epoch, sequence] = cursor.split('.');
  if (cursor && !Number.isSafeInteger(Number(sequence))) return new Response('Invalid event cursor', {status: 400});
  let after = cursor ? epoch === bus.epoch ? Number(sequence) : bus.sequence + 1 : Math.max(0, bus.sequence - 1);
  let closed = false, pumping = false, heartbeat, lifetime, controller;
  const subscriber = {wake() { pump(); }};
  function cleanup() {
    closed = true; bus.subscribers.delete(subscriber);
    clearInterval(heartbeat); clearTimeout(lifetime);
    request.signal.removeEventListener('abort', close);
  }
  function close() { if (!closed) { cleanup(); try { controller.close(); } catch {} } }
  function send(bytes) {
    if (closed) return false;
    // A byte-sized high water mark bounds pending output; history remains shared.
    if (controller.desiredSize < bytes.byteLength) return false;
    try { controller.enqueue(bytes); return true; } catch { cleanup(); return false; }
  }
  function pump() {
    if (closed || pumping) return;
    pumping = true;
    try {
      const oldest = bus.history[0]?.sequence || 1;
      if (after < oldest - 1 || after > bus.sequence) {
        if (!send(frame('neokit-reset', '{"reason":"replay-window"}'))) return;
        after = Math.max(0, bus.sequence - 1);
      }
      for (const event of bus.history) {
        if (event.sequence <= after) continue;
        if (!send(event.bytes)) break;
        after = event.sequence;
      }
    } finally { pumping = false; }
  }
  const stream = new ReadableStream({
    start(value) {
      controller = value;
      send(encoder.encode('retry: 1000\n\n'));
      send(frame('neokit-ready', JSON.stringify({channel: 'fixture', latest: bus.sequence})));
      bus.subscribers.add(subscriber);
      heartbeat = setInterval(() => send(encoder.encode(': heartbeat\n\n')), 15000);
      lifetime = setTimeout(close, 60000);
      request.signal.addEventListener('abort', close, {once: true});
      if (request.signal.aborted) close(); else pump();
    },
    pull() { pump(); },
    cancel() { cleanup(); }
  }, {highWaterMark: 49152, size: bytes => bytes.byteLength});
  return new Response(stream, {headers: {'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'}});
}
