# Environment, 31 October 2026

Vultr dedicated-CPU instance `voc-c-2c-4gb-50s-amd` (instance 23): 2 vCPUs, AMD EPYC Genoa, Ubuntu 24.04.5, Linux 6.8.0-146.

- SSR: server pinned to CPU 0, `wrk -t1 -c32` pinned to CPU 1; 2 s warm-up and 10 s measured, 5 interleaved rounds, medians.
  VifKit (this compiler, clang 18.1.3), Bun 1.3.13 (`Bun.serve`), Go 1.25.1 (`net/http`), Rust 1.99.1 (hyper 1.12.0, tokio 1.54.1, one worker thread).
  PostgreSQL and MongoDB were stopped during the SSR run.
- Database: every engine and its server pinned to CPU 1; fresh database per trial; trial 0 is a warm-up; medians of 5.
  SQLite 3.46.1 (go-sqlite3 1.14.24, WAL, synchronous=FULL), PostgreSQL 16.15 (pgx 5.7.2, synchronous commits),
  MongoDB 8.0.34 (mongo-driver 1.17.1, one-node replica set, j:true).
