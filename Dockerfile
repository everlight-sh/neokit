# syntax=docker/dockerfile:1
# docker build --build-context neo="$NEO_HOME" --build-context neo-libs=../axon-libs -t neokit .
FROM oven/bun:1.3.13 AS bun-tools
FROM ubuntu:24.04 AS toolchain
RUN apt-get update && apt-get install -y --no-install-recommends python3 clang lld binutils ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=bun-tools /usr/local/bin/bun /usr/local/bin/bun
FROM toolchain AS build
COPY --from=neo /neo /toolchain/neo/neo
COPY --from=neo /bin /toolchain/neo/bin
COPY --from=neo /VERSION /toolchain/neo/VERSION
COPY --from=neo /runtime /toolchain/neo/runtime
COPY --from=neo /stdlib /toolchain/neo/stdlib
COPY --from=neo-libs /http-server /axon-libs/http-server
COPY --from=neo-libs /web-security /axon-libs/web-security
COPY --from=neo-libs /web-cache /axon-libs/web-cache
COPY --from=neo-libs /document-store /axon-libs/document-store
COPY --from=neo-libs /postgres /axon-libs/postgres
COPY --from=neo-libs /oauth2 /axon-libs/oauth2
COPY --from=neo-libs /jwt /axon-libs/jwt
ENV NEO_HOME=/toolchain/neo NEO_THREADS=1
WORKDIR /web
COPY . .
RUN bun install --frozen-lockfile
ARG APP=examples/studio
RUN ./neokit build --app "$APP" --out build/release && test -z "$(nm -u build/release/server)" && ! readelf -l build/release/server | grep -q INTERP && ! readelf -d build/release/server | grep -q NEEDED
RUN "$NEO_HOME/neo" build tools/healthcheck.neo -o build/release/healthcheck
RUN if [ ! -f build/release/migrate ]; then echo "effect fn main()->unit=noop()" > build/release/noop.neo; "$NEO_HOME/neo" build build/release/noop.neo -o build/release/migrate; fi
RUN if [ ! -f build/release/revalidate ]; then printf '%s\n' 'import "std:process"' 'effect fn main()->unit=let error=print_error("Application has no stale-cache routes\n") in process_exit(1)' > build/release/no-revalidation.neo; "$NEO_HOME/neo" build build/release/no-revalidation.neo -o build/release/revalidate; fi
RUN mkdir -p /runtime-data /runtime-cache && chown 65532:65532 /runtime-data /runtime-cache

FROM build AS test-tools
CMD ["sh", "tests/verify_linux.sh"]

FROM scratch AS release
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /web/build/release/server /server
COPY --from=build /web/build/release/migrate /migrate
COPY --from=build /web/build/release/revalidate /revalidate
COPY --from=build /web/build/release/healthcheck /healthcheck
COPY --chown=65532:65532 --from=build /runtime-data /data
COPY --chown=65532:65532 --from=build /runtime-cache /cache
ENV NEO_THREADS=1
USER 65532:65532
EXPOSE 8094
ENTRYPOINT ["/server"]
CMD ["8094"]
