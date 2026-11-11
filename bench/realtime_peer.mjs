// Diagnostic fast peer. Exact wire payload/order checks, no per-recipient JSON
// decoding. Deliberately separate from the original Python peer.
import http from 'node:http';
import {performance} from 'node:perf_hooks';
const port = Number(process.argv[2]), clients = Number(process.argv[3] || 1000);
const duration = Number(process.argv[4] || 4), rate = 10;
const agent = new http.Agent({keepAlive: true, maxSockets: 1});
const requests = [], streams = [];
let sequence = 0, expected = '', sent = 0, received = 0, delivered;
let latencies = [], cohort = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function call(path, body) {
  return new Promise((resolve, reject) => {
    const request = http.request({host: '127.0.0.1', port, path, method: body ? 'POST' : 'GET', agent,
      headers: body ? {'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body)} : {}}, response => {
      let wire = ''; response.setEncoding('utf8'); response.on('data', part => wire += part);
      response.on('end', () => response.statusCode === 200 ? resolve(wire) : reject(Error(`HTTP ${response.statusCode}: ${wire}`)));
      response.on('error', reject);
    });
    request.on('error', reject); request.end(body);
  });
}
function subscribe(index) {
  return new Promise((resolve, reject) => {
    const request = http.get({host: '127.0.0.1', port, path: '/feed', agent: false, headers: {Cookie: 'session=allowed'}}, response => {
      streams.push(response);
      if (response.statusCode !== 200) throw Error(`SSE status ${response.statusCode}`);
      let pending = '', last = 0, ready = false;
      response.setEncoding('utf8');
      response.on('data', part => {
        pending += part;
        let end;
        while ((end = pending.indexOf('\n\n')) >= 0) {
          const frame = pending.slice(0, end); pending = pending.slice(end + 2);
          let event, data;
          for (const line of frame.split('\n')) {
            if (line.startsWith('event: ')) event = line.slice(7);
            if (line.startsWith('data: ')) data = line.slice(6);
          }
          if (event === 'neokit-ready') { if (ready) throw Error('Duplicate ready'); ready = true; resolve(); }
          else if (event === 'neokit-reset') throw Error('Unexpected reset');
          else if (event === 'message') {
            const at = performance.now();
            if (data !== expected || last + 1 !== sequence) throw Error(`Payload/order/duplicate mismatch on ${index}`);
            last = sequence; latencies.push(at - sent); received++;
            if (received === clients) { cohort.push(at - sent); delivered(); }
            else if (received > clients) throw Error('Too many deliveries');
          }
        }
      });
      response.on('error', reject);
      response.on('end', () => { throw Error('Unexpected stream termination'); });
    });
    requests.push(request); request.on('error', reject);
  });
}
async function publish() {
  sequence++; received = 0;
  expected = JSON.stringify({counter: sequence, text: '<café> & live', padding: 'x'.repeat(128)});
  const complete = new Promise(resolve => delivered = resolve);
  sent = performance.now();
  let timeout;
  try {
    await Promise.race([
      Promise.all([call('/publish', expected), complete]),
      new Promise((_, reject) => timeout = setTimeout(() => reject(Error('Delivery timeout')), 5000))
    ]);
  } finally { clearTimeout(timeout); }
}
try {
  for (let index = 0; index < clients; index += 32)
    await Promise.all(Array.from({length: Math.min(32, clients - index)}, (_, j) => subscribe(index + j)));
  for (let i = 0; i < 5; i++) await publish();
  await delay(2000);
  latencies = []; cohort = [];
  const cpu = process.cpuUsage(), began = performance.now();
  for (let index = 0; index < duration * rate; index++) {
    const wait = began + index * 1000 / rate - performance.now();
    if (wait > 0) await delay(wait);
    await publish();
  }
  await delay(Math.max(0, began + duration * 1000 - performance.now()));
  const elapsed = performance.now() - began, used = process.cpuUsage(cpu);
  if (latencies.length !== clients * duration * rate) throw Error('Missing events');
  latencies.sort((a, b) => a - b); cohort.sort((a, b) => a - b);
  const p = (values, fraction) => values[Math.min(values.length - 1, Math.floor(values.length * fraction))];
  console.log(JSON.stringify({clients, deliveries: latencies.length, duration_ms: elapsed,
    delivery_p50_ms: p(latencies, .5), delivery_p99_ms: p(latencies, .99),
    all_subscribers_p50_ms: p(cohort, .5), all_subscribers_p99_ms: p(cohort, .99),
    client_cpu_seconds: (used.user + used.system) / 1e6,
    client_cpu_percent_one_core: (used.user + used.system) / (elapsed * 1000) * 100,
    peer: `${process.versions.bun ? `Bun ${process.versions.bun}` : `Node ${process.version}`}, node:http, exact wire payload/order validation`}));
} finally {
  for (const response of streams) { response.removeAllListeners('end'); response.destroy(); }
  for (const request of requests) request.destroy();
  agent.destroy();
}
