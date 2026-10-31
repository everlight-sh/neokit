# syntax=docker/dockerfile:1
# docker build --build-context axon=../axon -t axon-web .
FROM oven/bun:1.3.13 AS bun-tools
FROM ubuntu:24.04 AS build
RUN apt-get update && apt-get install -y --no-install-recommends python3 clang lld binutils && rm -rf /var/lib/apt/lists/*
COPY --from=bun-tools /usr/local/bin/bun /usr/local/bin/bun
COPY --from=axon /axonc /toolchain/axon/axonc
COPY --from=axon /runtime /toolchain/axon/runtime
COPY --from=axon /stdlib /toolchain/axon/stdlib
ENV AXON_HOME=/toolchain/axon AXON_THREADS=1
WORKDIR /web
COPY . .
RUN bun install --frozen-lockfile
RUN ./web build && test -z "$(nm -u build/studio/server)" && ! readelf -l build/studio/server | grep -q INTERP && ! readelf -d build/studio/server | grep -q NEEDED

FROM build AS test-tools
CMD ["sh", "tests/verify_linux.sh"]

FROM scratch AS release
COPY --from=build /web/build/studio/server /server
ENV AXON_THREADS=1
USER 65532:65532
EXPOSE 8094
ENTRYPOINT ["/server"]
CMD ["8094"]
