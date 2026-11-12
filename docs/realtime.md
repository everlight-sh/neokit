# Realtime channels

NeoKit serves the initial page with native SSR, then sends named JSON events
over one Server-Sent Events connection. There is no Redis, broker process,
browser framework requirement or server JavaScript engine.

## Subscribe on the server

Declare `app/api/events/route.toml`:

```toml
handler="workspace_events"
methods=["GET"]
realtime=true
```

The handler returns streaming wire bytes, as with other custom stream handlers:

```neo
import "pkg:neokit/realtime"
effect fn workspace_events(request:WebRequest)->Arr u8 _
  = web_subscribe(request,web_channel("workspace"))
```

Inside this repository, examples can import `src/realtime.neo` by relative path.
The route flag enables channel storage before native HTTP workers fork, selects
stream dispatch, and gives HEAD the correct event-stream content type. GET is
the supported method; HEAD does not create a subscription. Static export omits
the endpoint. Do not combine this flag with `async=true` or typed action input:
stream admission and its before hook must complete promptly. Other async
pages/APIs and HTML streaming remain independent features.

Standard `[server] before="guard"` middleware runs before stream admission,
including HEAD. Use the application's session/authorization checks there.
EventSource sends same-origin cookies. Select an authorized channel in server
code; this API does not expose an unrestricted channel query or a public
publishing endpoint. Recheck authorization when the browser reconnects.

## Publish after an application change

```neo
let published=web_publish(web_channel("workspace"),"notice","{\"message\":\"Saved\"}") in
if published.ok then web_json("{\"ok\":true}") else
web_reply(503,"Could not publish update","text/plain")
```

Call publication after the application has committed its mutation. A database
commit and publication are separate operations; delivery is ephemeral and not
transactional with the database. Async Neo database/network tasks may publish
through this same API when they complete. Use snapshots to recover state.

Payloads must be valid JSON within the configured UTF-8 byte bound. Channel and
event names are 1–48 ASCII letters, digits, `.`, `:`, `_` or `-`. Event names
starting `neokit-` are reserved. Publishing returns a sequence or an explicit
error, including EAGAIN if a shared channel is briefly busy; callers can retry
from a cooperative task or report the failure.

## Subscribe in the browser

Import `subscribe` from `client/realtime.js`, or use `context.subscribe` inside a
normal NeoKit widget. The widget context closes its subscriptions on disposal:

```javascript
export function mount(element, context) {
  context.subscribe('/api/events', {
    events: {
      notice: {
        validate: value => value !== null && typeof value.message === 'string',
        receive: value => { element.textContent = value.message; }
      }
    },
    onStatus: state => { element.dataset.connection = state; },
    onReset: () => { /* refetch your authoritative snapshot */ },
    onError: error => { /* report malformed data without rendering it */ }
  });
}
```

The direct helper returns `{pause(), resume(), close(), cursor}`. It validates
same-origin HTTP URLs, UTF-8 payload size (8 KiB default, configurable up to
64 KiB), JSON, event IDs, and the supplied application validator. It ignores
duplicate/stale IDs and callbacks from replaced connections. Named handlers
share one connection. Events are application data, never inserted as HTML.

Connections pause while the document is hidden by default, resume with their
cursor on visibility, and close on pagehide/widget disposal. EventSource handles
normal retries; terminal failures use exponential reconnect delays from 1 to
30 seconds. `pauseWhenHidden=false` keeps background subscriptions running.
States are `connecting`, `live`, `reconnecting`, `paused`, and `closed`.

A successful publication signals the server workers' existing IPC sockets,
which are watched by kqueue/epoll. Only subscribers whose channel changed are
scheduled. There is no periodic subscriber data check. Arming a subscription
checks for a publication racing with the history copy, so that transition cannot
lose a wakeup. Brief channel contention waits for an unlock notification.

Up to four events are copied per callback; a remaining backlog schedules another
turn immediately after queued bytes drain. Slow sockets resume on kernel write
readiness. Idle heartbeat deadlines emit comments every 15 seconds without
reading channel data. Connections end around 60 seconds for renewal. The browser
reconnects with `Last-Event-ID`; manual resume uses the `after` query cursor. The header
takes precedence. Cursors identify the server epoch and event sequence.
Out-of-window, future and previous-epoch cursors emit `neokit-reset` and the
latest available event. Applications using deltas must refetch a snapshot in
`onReset`; a replay window is not a durable queue or an exactly-once guarantee.

The common single-event path keeps its channel/cursor/deadline in Neo's retained
scalar continuation state and reuses a prepared HTTP/SSE frame from
`std:event_stream`. It does not parse/rebuild JSON state or encode the same
cached frame for each recipient. The per-worker cache holds up to 64 frames,
with exact content keys and PID/generation-checked references. Encoding is
repeated after eviction. Writable sockets borrow cached bytes during a
synchronous send; an unsent suffix is copied into connection-owned storage.

Replay batches and large events use bounded assembly. A frame spanning the
48 KiB callback budget resumes at its byte offset without advancing the cursor
until the complete event is sent. If retention is lost during a partial event,
the stream closes and reconnects so fragments of different events cannot mix.
The callback state/capture format is internal; low-level wrappers using
`web_subscribe_with` should forward to `web_events_step` instead of decoding it.

## Storage and limits

Optional `web.toml` settings:

```toml
[realtime]
channels=16
history=128
max_event_bytes=4096
```

The backend is bounded anonymous shared memory, initialized before fork. A
server family shares channel names, sequence IDs, history and publications
across its native workers. Storage is lost on full server restart. Separate
server instances/machines do not share this backend. There is no external
delivery adapter or WebSocket transport in this version.

Configuration permits 1–64 channels, 1–1,024 retained events per channel and
64–8,192 payload bytes per event, with total reserved storage limited to 64 MiB.
Channel creation and publication use short nonblocking try-locks. A worker
crashing while holding a lock fails that channel closed until restart; there is
no lock-owner recovery protocol. No user callback runs while a channel lock is
held. Normal cancellation and disconnects cannot strand a channel lock.

Subscribers use small event continuations without task stacks, with timers only
for heartbeat and stream renewal. Each worker's
HTTP profile supports 1,024 simultaneous connections, including ordinary
requests. Pending event output stays below 64 KiB per connection; a slow reader
does not accumulate an unbounded per-subscriber history. It can instead need a
snapshot reset after falling behind. Proxy buffering must remain disabled;
the endpoint sets `X-Accel-Buffering: no` and `Cache-Control: no-store`.

The server-only primitives are reusable independently of NeoKit:
`std:events` provides configuration, named channels, publication, deduplication
keys and bounded reads; `std:sse` provides encoding/decoding; `std:http_async`
provides byte-oriented recurring responses, retained scalar state, prepared
frames and lifecycle cleanup; `std:event_stream` composes them into cached
event-channel frames.

## Meridian example

`examples/saas/live.neo` uses the ordinary `web_subscribe` API. A separate
process-owned Neo timer generates one synthetic event every two seconds in
worker zero and calls the same publication API as an application mutation.
The timer is a demo data source; delivery is publish-triggered. It runs even
when no browser is subscribed, so pausing a tab does not pause the producer.

`[server] startup="saas_start"` imports a startup function from `app/hooks.neo`.
It runs once before worker forks and returns `IntResult`. This example registers
its short producer callback through Neo's `std:timer` library. Timer callbacks
must return promptly without blocking I/O; ordinary cooperative database/network
producers can publish as soon as their I/O completes. Periodic callbacks stop
when shutdown draining begins. Static export does not execute startup hooks.

Live counters carry absolute demo totals, so the latest event restores their
state after a gap. The live chart retains 60 received samples and the activity
feed 12 rows. Seeded historical revenue/customer data remains a separate
read-only dataset. Static exports show a snapshot explanation and open no live
connection.

Release measurements and verification in the `neokit-bench` repository
cover unchanged SSR pages, 1/100/1,000 live subscribers, replay, cancellation,
Linux execution, actual-browser behavior and the limits of these measurements.
