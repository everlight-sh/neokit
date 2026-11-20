# NeoKit 0.1.0-alpha.1

NeoKit builds native Neo servers and static sites. This alpha includes a source SDK, the Meridian analytics demo, and a standalone starter. The supported deployment profile is one Linux host with native HTTP/1.1 workers behind Caddy. Realtime shares a bounded replay window across workers on the same host. A full restart resets the replay history. Async database and network waits yield to the worker. Regular disk I/O and PostgreSQL result buffering are part of the current profile.

## Install and create an app

Use Bun 1.3.13 and Clang/LLVM with a platform linker. Linux builds use LLD. The SDK contains the native Neo 0.2.11 compiler binaries for macOS ARM64 and Linux ARM64/x86-64, runtime sources, exact local libraries, and browser assets. The `neokit` command is a small sh launcher: its first run builds NeoKit's native CLI from the SDK's sources with the bundled Neo compiler, which takes a minute or so, and caches it under `${XDG_CACHE_HOME:-~/.cache}/neokit/cli`. The Neo compiler, the CLI and the deployed app execute natively. Verify the published SHA-256 of the archive before unpacking it.

```sh
tar -xzf neokit-0.1.0-alpha.1.tar.gz
cd neokit-0.1.0-alpha.1
./install.sh "$HOME/.local"
"$HOME/.local/bin/neokit" doctor
"$HOME/.local/bin/neokit" init --standalone "$HOME/first-neokit-app"
cd "$HOME/first-neokit-app"
./neokit build
./neokit serve --no-build --port 8094
```

The starter's `vendor/neokit-sdk` directory is part of the project. Keep it with the application sources when moving the project. The project launcher selects that SDK and writes builds under `.neokit/build`. The plain CSS starter builds with the installed tools and no package downloads. Optional Tailwind builds use `bun install --frozen-lockfile` in the SDK framework directory. Image variants additionally require libvips (`vips` and `vipsheader`).

Use `./neokit dev` for development. Use `./neokit export` to generate a static snapshot in the sibling directory named after your project with an `-export` suffix. See the framework README in `vendor/neokit-sdk/framework/README.md` for routes, templates, actions, and streaming examples.

## Deploy Meridian over HTTPS

On a Linux host, install Docker with BuildKit and Compose support for `additional_contexts`. Point your hostname at the host and make ports 80 and 443 reachable. From the extracted SDK directory run:

```sh
export DOMAIN=meridian.example.com
docker compose -f framework/deploy/meridian.compose.yaml up --build -d
docker compose -f framework/deploy/meridian.compose.yaml ps
curl --fail "https://$DOMAIN/healthz"
```

Caddy obtains the public certificate. The app container serves synthetic data and exposes only its proxy to the host. The proxy allows GET and HEAD. Native asset compression remains available and streaming responses can reach the client as they arrive. Keep the Caddy data volume for certificate renewal. The 35-second app stop grace allows the native 30-second shutdown deadline to run. To roll back, retain the previous tested app image, set `NEOKIT_APP_IMAGE` to its tag or digest, and run `up -d --no-build` with the same Compose file.

For isolated HTTPS verification, use `DOMAIN=localhost`, `NEOKIT_BIND_IP=127.0.0.1`, and available `NEOKIT_HTTP_PORT`/`NEOKIT_HTTPS_PORT` values. Add `-f framework/deploy/meridian.local.compose.yaml` to the Compose command. Read the local CA certificate using `docker compose ... cp proxy:/data/caddy/pki/authorities/local/root.crt ./local-ca.crt` and pass `--cacert ./local-ca.crt` to curl. The local profile uses a private CA. Public DNS and certificate issuance require the real host and domain.

For the alpha, keep public demos read-only. Authenticated applications need application-level authorization, private cache rules, protected credentials, monitored resource limits, and a backup/restore policy. See [SECURITY.md](../SECURITY.md) in the framework directory.
