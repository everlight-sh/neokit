# Async mixed-load measurements

One NeoKit worker, production prepared libraries, 32 fast-route connections and one wrk thread. 1 shuffled rounds; one second warmup then 4 seconds timing. Independent delayed HTTP peers and real PostgreSQL SCRAM queries (`pg_sleep(0.6)`). Background clients reopen their route continuously.

| Background work | Clients | Fast req/s (median) | Fast p99 ms | Peak sampled RSS MiB | Total server CPU % one core |
|---|---:|---:|---:|---:|---:|
| idle | 0 | 103,170.0 | 7.420 | 3.5 | 83.73 |
| network-waits | 32 | 109,006.1 | 1.040 | 5.4 | 89.76 |
| database-and-network-streams | 16 | 104,436.7 | 16.000 | 6.4 | 91.97 |

CPU includes fast responses and all concurrent loader/stream work; it is not CPU attributable only to the fast route. PostgreSQL and peer-process resources are excluded from NeoKit RSS/CPU. RSS is sampled every 250 ms. All routes validate status/body, drain to zero live tasks/capture bytes/result bytes after each trial, and reject wrk errors. This is a busy shared Mac, not a universal capacity claim. [Raw measurements](results.json).
