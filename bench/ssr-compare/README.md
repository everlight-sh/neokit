# SSR comparison: framework vs Bun, Go and Rust

The catalog benchmark from `bench/frameworks` (`/`, `/large`, `/interactive`),
rendered by four servers that produce the same HTML:

- the framework's own server (`./web build --app bench/frameworks/apps/axon`)
- `bun/server.ts`: Bun.serve with template strings
- `go/`: net/http with an appended byte buffer (`go build -o catalog-ssr .`)
- `rust/`: hyper 1 on tokio, string building

Each request parses `n`, `seed` and `token`, builds `n` escaped rows, sums a
checksum and stamps the current time; `bench/frameworks/model.py` validates
every response. `run.py` interleaves rounds, pins the server to one CPU and
wrk to another, and records req/s, latency and server CPU per request.
