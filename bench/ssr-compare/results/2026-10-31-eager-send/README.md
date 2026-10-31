# Third run, 31 October 2026: large responses sent eagerly

Same instance type, image and versions as `../2026-10-31/` (Vultr `voc-c-2c-4gb-50s-amd`, AMD EPYC
Genoa, Ubuntu 24.04.5, Linux 6.8; Bun 1.3.13, Go 1.25.1, Rust 1.99.1), on a fresh instance.

The runtime now sends a large keep-alive response (16 KiB or more, nothing queued ahead of it)
straight to the socket with MSG_DONTWAIT and buffers only what the kernel does not take. Before,
every response was copied into its connection's output buffer, which kept its largest size: with 32
connections on the 105 KB page that held about 4 MiB (peak 4.18 MiB against Rust's 3.51 MiB).
Now peak memory is 0.93 MiB on the large page. Interleaved A/B runs of the old and new runtime on
this instance gave the same throughput; every eager send of the large page went out whole.

Checked with tests/large_responses.py (io_uring and epoll: pipelined responses to a slow reader,
closing connections, 32 concurrent clients), the compiler's unit tests, its HTTP smoke test and
./web test.

Deployed size on this instance: build/catalog/server 164,120 bytes (static, assets embedded); Rust
936,016; Go 8,127,311; Bun runtime 101,814,712 plus the 3,109-byte script.

Small page: VifKit 78,913 req/s, Rust 82,405. CPU per request is equal (11.9 and 12.0 µs); the page
is bound by the kernel's TCP path and the single load-generator thread (on the first instance
VifKit led, 78,456 to 73,717).
