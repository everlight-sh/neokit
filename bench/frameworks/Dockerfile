# syntax=docker/dockerfile:1
FROM oven/bun:1.3.13 AS bun-tools
FROM node:24-bookworm-slim AS node-tools
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends python3 clang lld binutils wrk procps time util-linux ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=bun-tools /usr/local/bin/bun /usr/local/bin/bun
COPY --from=node-tools /usr/local/bin/node /usr/local/bin/node
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 ASTRO_TELEMETRY_DISABLED=1 AXON_THREADS=1 AXON_HOME=/work/axon
WORKDIR /work/axon-web
CMD ["sleep", "infinity"]
