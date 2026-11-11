# Async mixed-load measurements

One NeoKit worker, production prepared libraries, 32 fast-route connections and one wrk thread. Three shuffled rounds; one second warmup then four seconds timing. Independent delayed HTTP peers and real PostgreSQL SCRAM queries (`pg_sleep(0.6)`). Background clients reopen their route continuously.

| Background work | Clients | Fast req/s (median) | Fast p99 ms | Peak sampled RSS MiB | Total server CPU % one core |
|---|---:|---:|---:|---:|---:|
| idle | 0 | 114,808.6 | 0.568 | 4.8 | 88.34 |
| network-waits | 32 | 113,098.9 | 0.483 | 6.8 | 92.49 |
| database-and-network-streams | 16 | 135,707.3 | 4.140 | 7.2 | 92.72 |

CPU includes fast responses and all concurrent loader/stream work; it is not CPU attributable only to the fast route. PostgreSQL and peer-process resources are excluded from NeoKit RSS/CPU. RSS is sampled every 250 ms. All routes validate status/body, drain to zero live tasks/capture bytes/result bytes after each trial, and reject wrk errors. This is a busy shared Mac, not a universal capacity claim. [Raw measurements](results.json).
