#!/usr/bin/env python3
"""Catalog SSR: the framework's server vs Bun, Go (net/http) and Rust (hyper), one server CPU each.

Validates every server with bench/frameworks/model.py (rows, escaping, checksum,
token, fresh timestamp) and checks the bodies are byte-identical apart from
the timestamp, then runs interleaved wrk rounds with the server pinned to one CPU
and wrk to another. Records req/s, latency, server CPU per request and peak memory.

  python3 bench/ssr-compare/run.py --framework build/catalog/server \
      --bun bench/ssr-compare/bun/server.ts --go bench/ssr-compare/go/catalog-ssr \
      --rust bench/ssr-compare/rust/target/release/catalog-ssr \
      --server-cpu 0 --client-cpu 1 --rounds 5 --out build/ssr-compare
"""
import argparse, json, os, re, signal, socket, statistics, subprocess, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "frameworks"))
from model import SCENARIOS, validate  # noqa: E402

TICK = os.sysconf("SC_CLK_TCK")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def group_cpu(pgid):
    total = 0
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                f = open(f"/proc/{d}/stat").read().rsplit(")", 1)[1].split()
            except OSError:
                continue
            if int(f[2]) == pgid:
                total += int(f[11]) + int(f[12])
    return total / TICK


def group_peak_kib(pgid):
    """Sum of peak resident memory (VmHWM) over the server's process group."""
    total = 0
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                if int(open(f"/proc/{d}/stat").read().rsplit(")", 1)[1].split()[2]) != pgid: continue
                total += next(int(l.split()[1]) for l in open(f"/proc/{d}/status") if l.startswith("VmHWM:"))
            except (OSError, StopIteration, ValueError):
                continue
    return total


def start(cmd, cpu, env):
    port = free_port()
    proc = subprocess.Popen(["taskset", "-c", str(cpu), *cmd, str(port)], env={**os.environ, **env},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(300):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close(); break
        except OSError:
            time.sleep(0.02)
    return proc, port


def stop(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM); proc.wait(timeout=5)
    except Exception:
        os.killpg(proc.pid, signal.SIGKILL)


def fetch(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return r.status, r.read()


def unstamp(body):
    return re.sub(rb'id="render-stamp">\d+<', b'id="render-stamp">0<', body)


def unit_us(v):
    m = re.match(r"([\d.]+)(us|ms|s)", v); return float(m[1]) * {"us": 1, "ms": 1000, "s": 1e6}[m[2]]


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--framework", required=True); a.add_argument("--bun", required=True); a.add_argument("--go", required=True); a.add_argument("--rust", required=True)
    a.add_argument("--name", default="Framework"); a.add_argument("--server-cpu", type=int, default=0); a.add_argument("--client-cpu", type=int, default=1)
    a.add_argument("--rounds", type=int, default=5); a.add_argument("--seconds", type=int, default=10); a.add_argument("--out", type=Path, required=True)
    args = a.parse_args(); args.out.mkdir(parents=True, exist_ok=True)

    # The framework's CSS link carries a content hash; the others use the same href.
    proc, port = start([args.framework], args.server_cpu, {"NEO_THREADS": "1"})
    css = re.search(rb'<link rel="stylesheet" href="([^"]+)"', fetch(port, "/")[1])[1].decode()
    js = re.search(rb'<script type="module" src="([^"]+)"', fetch(port, "/interactive")[1])[1].decode(); stop(proc)
    servers = {
        args.name: ([args.framework], {"NEO_THREADS": "1"}),
        "Bun": (["bun", args.bun], {"CSS_HREF": css, "JS_HREF": js}),
        "Go": ([args.go], {"CSS_HREF": css, "JS_HREF": js, "GOMAXPROCS": "1"}),
        "Rust": ([args.rust], {"CSS_HREF": css, "JS_HREF": js, "TOKIO_WORKER_THREADS": "1"}),
    }

    # Correctness: every server, every scenario, several inputs; identical bodies.
    cases = [(s, f"{s['path']}?n={n}&seed={seed}&token={tok}", n, seed, tok)
             for s in SCENARIOS for n, seed, tok in [(s["n"], 17, "compile-benchmark"), (7, 3, "second-token"), (1000, 999, "x")]]
    bodies = {}
    for name, (cmd, env) in servers.items():
        proc, port = start(cmd, args.server_cpu, env)
        try:
            for s, path, n, seed, tok in cases:
                status, body = fetch(port, path)
                assert status == 200, (name, path, status)
                validate(body, n, seed, urllib.request.unquote(tok), s["interactive"])
                bodies.setdefault(path, {})[name] = unstamp(body)
        finally:
            stop(proc)
    for path, per in bodies.items():
        assert len(set(per.values())) == 1, ("bodies differ", path, {k: len(v) for k, v in per.items()})
    print(f"validated {len(cases)} requests per server; bodies byte-identical across servers", flush=True)

    rows = []
    for s in SCENARIOS:
        path = f"{s['path']}?n={s['n']}&seed=17&token=compile-benchmark"
        size = len(next(iter(bodies[path].values())))
        for r in range(args.rounds):
            order = list(servers.items()); order = order if r % 2 == 0 else order[::-1]
            for name, (cmd, env) in order:
                proc, port = start(cmd, args.server_cpu, env)
                try:
                    wrk = ["taskset", "-c", str(args.client_cpu), "wrk", "-t1", "-c32", "--latency", "--timeout", "5s"]
                    subprocess.run(wrk + ["-d2s", f"http://127.0.0.1:{port}{path}"], capture_output=True)
                    c0 = group_cpu(proc.pid)
                    out = subprocess.run(wrk + [f"-d{args.seconds}s", f"http://127.0.0.1:{port}{path}"], capture_output=True, text=True).stdout
                    c1 = group_cpu(proc.pid); peak = group_peak_kib(proc.pid)
                finally:
                    stop(proc)
                assert "Socket errors" not in out and "Non-2xx" not in out, (name, out)
                n = int(re.search(r"(\d+) requests in", out)[1])
                pct = {k: unit_us(v) for k, v in re.findall(r"^\s+(50|99)%\s+(\S+)", out, re.M)}
                rows.append({"scenario": s["name"], "server": name, "round": r, "bytes": size,
                             "rps": float(re.search(r"Requests/sec:\s+([\d.]+)", out)[1]),
                             "p50_us": pct["50"], "p99_us": pct["99"], "cpu_us": (c1 - c0) / n * 1e6, "peak_mib": peak / 1024})
                print(s["name"], name, r, round(rows[-1]["rps"]), "req/s", flush=True)
    (args.out / "samples.json").write_text(json.dumps(rows, indent=2))
    summary = []
    for s in SCENARIOS:
        for name in servers:
            v = [x for x in rows if x["scenario"] == s["name"] and x["server"] == name]
            summary.append({"scenario": s["name"], "server": name, "bytes": v[0]["bytes"],
                            **{k: statistics.median(x[k] for x in v) for k in ("rps", "p50_us", "p99_us", "cpu_us", "peak_mib")}})
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    for x in summary:
        print(f"{x['scenario']:16} {x['server']:10} {x['rps']:9.0f} req/s  p50 {x['p50_us']:7.0f}  p99 {x['p99_us']:7.0f} us  cpu {x['cpu_us']:7.1f} us/req  peak {x['peak_mib']:6.1f} MiB  {x['bytes']} B")


if __name__ == "__main__":
    main()
