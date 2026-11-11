# Async mixed-load measurements

One NeoKit worker, production prepared libraries, 32 fast-route connections and one wrk thread. 1 shuffled rounds; one second warmup then 4 seconds timing. Independent delayed HTTP peers and real PostgreSQL SCRAM queries (`pg_sleep(0.6)`). Background clients reopen their route continuously.

| Background work | Clients | Fast req/s (median) | Fast p99 ms | Peak sampled RSS MiB | Total server CPU % one core |
|---|---:|---:|---:|---:|---:|
| idle | 0 | 97,862.0 | 3.010 | 3.1 | 83.18 |
| network-waits | 32 | 105,223.5 | 1.160 | 5.3 | 89.04 |
| database-and-network-streams | 16 | 112,992.1 | 12.710 | 6.4 | 95.14 |

CPU includes fast responses and all concurrent loader/stream work; it is not CPU attributable only to the fast route. PostgreSQL and peer-process resources are excluded from NeoKit RSS/CPU. RSS is sampled every 250 ms. All routes validate status/body, drain to zero live tasks/capture bytes/result bytes after each trial, and reject wrk errors. This is a busy shared Mac, not a universal capacity claim. [Raw measurements](results.json).
