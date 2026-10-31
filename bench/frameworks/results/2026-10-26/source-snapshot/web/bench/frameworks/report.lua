done = function(summary, latency, requests)
  io.write(string.format('BENCH {"requests":%d,"duration_us":%d,"bytes":%d,"p50_us":%.3f,"p95_us":%.3f,"p99_us":%.3f,"max_us":%.3f,"errors":{"connect":%d,"read":%d,"write":%d,"status":%d,"timeout":%d}}\n',
    summary.requests, summary.duration, summary.bytes,
    latency:percentile(50), latency:percentile(95), latency:percentile(99), latency.max,
    summary.errors.connect, summary.errors.read, summary.errors.write, summary.errors.status, summary.errors.timeout))
end
