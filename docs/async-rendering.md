# Async loaders and progressive HTML

Async rendering lets a loader wait for a database or network response while the
same server worker serves other requests. Streaming adds a second behavior:
send a shell immediately, then replace loading sections as independent loaders
finish. They can be used together or separately.

The reusable executor, socket suspension, cancellation and HTTP backpressure are
in [Neo's standard library](../../neo/docs/async-rendering.md). Existing native
HTTP(S), DNS and shared PostgreSQL clients suspend at their ordinary socket
waits; NeoKit does not maintain private async protocol implementations.

## Buffered async pages and APIs

Add to `page.toml` or `route.toml`:

```toml
async = true
async_timeout_ms = 2000
```

Loaders and handlers retain their usual signatures and typed templates. The
framework runs the request hook and loader in a cooperative task, then encodes
the complete response. There are no special promises or manual task states.
The default timeout is 10 s; the allowed range is 1–120,000 ms. Async pages
cannot use `mode="static"`; use `export=false` when export cannot run a loader.
See [the async page](../examples/features/app/async-page) and
[the async API](../examples/features/app/async-api).

## Streaming independent sections

Use an endpoint with both flags when initial work can also wait:

```toml
handler = "feature_async"
stream = true
async = true
async_timeout_ms = 10000
```

Import `src/stream.neo`. A deferred loader has this signature:

```neo
private effect(fs,net,env,time,process) fn load_section(capture:Arr u8 n)->Arr u8 _
  = let response=http_request("127.0.0.1",8080,"GET",capture,"",5000,1048576) in
    if !response.ok then let failed=async_fail(response.error.code) in "" else
    concat("<p>",html_escape(response.body),"</p>")
```

Create a section with `web_defer("network",web_html("<p>Loading…</p>"),"/data",function(load_section))`.
Put `web_boundary(section).bytes` inside your trusted shell HTML, then return
`web_stream_page(title,shell,parts,deadline.value)`. `parts` is an array of
`WebDeferred`. Each unique ID must occur in the shell; IDs are ASCII letters,
digits, hyphens or underscores, 1–64 bytes. Use
`web_defer_timeout(section,250)` for an individual section deadline. A loader
may use the native PostgreSQL client with scoped connection resources and
parameterized queries exactly as in synchronous code.

The [complete example](../examples/features/app/async/route.neo) first waits for
an HTTP prelude, then emits a shell with four sections: a real PostgreSQL query,
two network requests with different completion times, and a timed-out section.
The fastest section appears first, independently of declaration order. Its
inserted counter widget mounts as soon as its HTML arrives.

An external same-origin module processes inert `<template>` fragments after
their completion markers arrive. Response tokens prevent stale or duplicate
updates. Inserted widgets mount through the usual widget lifecycle. Executable
elements, inline handlers and unsafe URL schemes are rejected; text must still
be escaped by the server. `WebHtml` is trusted HTML, not a sanitizer. No inline
script relaxation is needed. Without JavaScript, the initial fallbacks remain.
Stream failures become generic section errors; protocol details stay private. Enhanced links fall back to full document navigation for streaming pages; the existing client router buffers its fetch and does not itself progressively consume the stream.
Successful headers already sent remain HTTP 200 when a section fails.

Raw chunk streaming is also available through Neo's `http_render_start` and
`http_render_write`. It yields to backpressure and does not expose the client
socket. The older borrowed transport is synchronous and is rejected inside an
async render task. See [the migrated raw stream](../examples/features/app/stream/route.neo).

## Supported profile

- Up to 32 sections per response, 256 live tasks per server worker. Capacity
  failure before commit returns 503. Buffered render deadlines return 504.
- Disconnects and shutdown deadlines cancel upstream waits and release task
  stacks, captures and results. Pending userland output is bounded at 64 KiB.
- HTTP/1.1 async responses close their connection. HEAD on a custom stream
  keeps the existing header-only behavior; buffered async HEAD executes its
  loader and omits the body.
- CPU work must yield/checkpoint. Regular disk reads/writes/fsync remain
  synchronous; contended document-store file locks yield. PostgreSQL result
  sets remain buffered. This does not add React/RSC, Suspense compatibility,
  row cursors, preemptive execution or distributed caching.
- Existing request logging measures async admission rather than final streamed
  delivery; it is not a per-section or completed-response tracing facility.

HTTP tests (`./neokit test --postgres`) exercise real SCRAM PostgreSQL, delayed
peers, timeouts, disconnects, HEAD/method dispatch and zero idle task counters.
Browser checks (`bun tests/tools/async_browser_fixture.ts`) cover streamed
insertion, escaped UTF-8, isolated errors and widget mounting. Performance and
verification results are in the `neokit-bench` repository.
