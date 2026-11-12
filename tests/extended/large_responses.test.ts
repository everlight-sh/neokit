// Large responses through both Linux HTTP backends (io_uring, and epoll with
// NEO_HTTP_URING=0). A large keep-alive response with nothing queued ahead of
// it is sent to the socket straight away and only the rest is buffered. With
// each backend: pipelined responses (large and small mixed) arrive whole and
// in order to a slow reader with a tiny receive buffer, so sends are cut
// short; Connection: close and HTTP/1.0 responses arrive whole before the
// close; 32 concurrent connections each fetch large pages. Every body is
// checked by the catalog oracle. Each backend gets a fresh port: a server
// restarted on a port an io_uring server has just left can see connections
// reset while the old ring tears down.
import { test, expect } from "bun:test";
import { join } from "node:path";
import { NetSocket, ROOT, RawSocket, neokit, pythonInt, running, sleep, type Receiver } from "../lib";
import { validate, verifyServer } from "./catalog_oracle";

const OUT = join(ROOT, "build/catalog");
neokit(["build", "--app", join(ROOT, "tests/catalog_fixture"), "--out", OUT]);
const binary = join(OUT, "server");
const BACKENDS: [string, Record<string, string>][] = [["io_uring", {}], ["epoll", { NEO_HTTP_URING: "0" }]];
// Up to 30 s to accept and 15 s to stop after SIGTERM, as the Python suites allowed.
const SERVER = { startup: 30000, stopTimeout: 15000 };

/** One response framed by Content-Length (the only framing these pages use); returns [body, leftover]. */
async function readResponse(socket: Receiver, buffer: Buffer): Promise<[Buffer, Buffer]> {
  while (buffer.indexOf("\r\n\r\n") < 0) {
    const chunk = await socket.recv();
    if (!chunk.length) throw new Error("EOFError: closed in headers");
    buffer = Buffer.concat([buffer, chunk]);
  }
  const split = buffer.indexOf("\r\n\r\n");
  const head = buffer.subarray(0, split);
  let rest = buffer.subarray(split + 4);
  const line = head.toString("latin1").split("\r\n").find(field => field.toLowerCase().startsWith("content-length"));
  if (line === undefined) throw new Error("StopIteration: no Content-Length in " + JSON.stringify(head.toString("latin1")));
  const length = pythonInt(line.split(":")[1]);
  while (rest.length < length) {
    const chunk = await socket.recv();
    if (!chunk.length) throw new Error("EOFError: closed in body");
    rest = Buffer.concat([rest, chunk]);
  }
  return [rest.subarray(0, length), rest.subarray(length)];
}

const requestBytes = (path: string, n: number, seed: number, token: string, extra = "", version = "HTTP/1.1") =>
  Buffer.from(`GET ${path}?n=${n}&seed=${seed}&token=${token} ${version}\r\nHost: x\r\n${extra}\r\n`);

/** Six pipelined requests to a reader with a 4 KiB receive buffer that waits before reading. */
async function pipelined(port: number) {
  const plan: [string, number][] = [["/large", 1000], ["/", 3], ["/large", 1000], ["/large", 700], ["/interactive", 50], ["/large", 1000]];
  const socket = RawSocket.connect(port, { receiveBuffer: 4096, timeout: 10000 });
  try {
    socket.sendAll(Buffer.concat(plan.map(([path, n], k) => requestBytes(path, n, k, `t${k}`))));
    await sleep(400); // let the socket fill, so sends are cut short
    let buffer = Buffer.alloc(0);
    for (const [k, [path, n]] of plan.entries()) {
      let body: Buffer;
      [body, buffer] = await readResponse(socket, buffer);
      validate(body, n, k, `t${k}`, path === "/interactive");
    }
  } finally { socket.close(); }
  return plan.length;
}

/** Connection: close and HTTP/1.0: the whole page arrives before the close. */
async function closing(port: number) {
  for (const [extra, version] of [["Connection: close\r\n", "HTTP/1.1"], ["", "HTTP/1.0"]]) {
    const socket = await NetSocket.connect(port, 10000);
    try {
      await socket.send(requestBytes("/large", 1000, 5, "close", extra, version));
      const chunks: Buffer[] = [];
      for (let chunk = await socket.recv(); chunk.length; chunk = await socket.recv()) chunks.push(chunk);
      const data = Buffer.concat(chunks);
      const split = data.indexOf("\r\n\r\n");
      expect([version, split >= 0]).toEqual([version, true]);
      validate(data.subarray(split + 4), 1000, 5, "close", false);
    } finally { socket.close(); }
  }
  return 2;
}

/** 32 connections, each fetching 20 large pages in turn. */
async function concurrent(port: number) {
  const errors: string[] = [];
  let done = 0;
  await Promise.all(Array.from({ length: 32 }, async (_, w) => {
    let socket: NetSocket | undefined;
    try {
      socket = await NetSocket.connect(port, 10000);
      let buffer = Buffer.alloc(0);
      for (let i = 0; i < 20; i++) {
        const seed = (w * 31 + i) % 1000;
        await socket.send(requestBytes("/large", 1000, seed, `w${w}`));
        let body: Buffer;
        [body, buffer] = await readResponse(socket, buffer);
        validate(body, 1000, seed, `w${w}`, false);
        done++;
      }
    } catch (error) {
      errors.push(String((error as Error)?.stack ?? error));
    } finally { socket?.close(); }
  }));
  expect(errors.slice(0, 3)).toEqual([]);
  expect(done).toBe(32 * 20);
  return done;
}

for (const [name, env] of BACKENDS) test(`${name}: pipelined, closing and concurrent large responses`, async () => {
  await running(binary, { ...SERVER, env }, async ({ port }) => {
    console.log(`PASS ${name}: ${await pipelined(port)} pipelined, ${await closing(port)} closing, ${await concurrent(port)} concurrent responses`);
  });
});

for (const [name, env] of BACKENDS) test(`${name}: catalog oracle on every scenario, with fresh renders`, async () => {
  await running(binary, { ...SERVER, env }, async ({ port }) => {
    expect(await verifyServer(port)).toBe(9);
  });
});
