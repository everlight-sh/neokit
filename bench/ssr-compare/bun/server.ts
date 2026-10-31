// Catalog SSR in Bun: same markup, escaping and query rules as the framework app.
const WORDS = ["Widget", "Gadget", "Gizmo", "Doohickey", "Sprocket", "Flange", "Bracket", "Grommet"];
const ADJECTIVES = ["Deluxe", "Basic", "Pro", "Mini", "Ultra", "Classic", "Eco", "Max"];
const SPECIAL = `Ben & Jerry's "Special" <limited> café`;
const CSS = process.env.CSS_HREF ?? "/assets/site.css";
const JS = process.env.JS_HREF ?? "/assets/client.js";

const escapeHtml = (s: string) =>
  s.replace(/[&<>"']/g, (c) => (c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : c === '"' ? "&quot;" : "&#x27;"));

function number(params: URLSearchParams, key: string, fallback: number, min: number, max: number): number {
  const v = params.get(key);
  if (v !== null && /^[0-9]{1,4}$/.test(v)) {
    const n = Number(v);
    if (n >= min && n <= max) return n;
  }
  return fallback;
}

function render(n: number, seed: number, token: string, interactive: boolean): string {
  let rows = "";
  let checksum = 0;
  for (let i = 0; i < n; i++) {
    const price = (i * 37 + seed * 13) % 1000;
    const stock = (i * 13 + seed) % 50;
    checksum += price + stock;
    rows += `<tr data-id="${i}"><td>${i}</td><td>${escapeHtml(WORDS[(i + seed) % 8] + " " + ADJECTIVES[(i * 7 + seed) % 8])}</td><td>${price}.99</td><td>${stock}</td><td>${escapeHtml(i % 5 === 0 ? SPECIAL : "-")}</td></tr>`;
  }
  const counter = interactive
    ? `<section class="counter" data-web-widget="counter"><output data-count aria-label="Counter value" aria-live="polite">0</output><button type="button" data-increment>Add one +</button><button type="button" data-reset>Reset</button></section>`
    : "";
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Framework catalog</title><link rel="stylesheet" href="${CSS}"></head><body><main id="benchmark" data-n="${n}" data-seed="${seed}"><h1>Framework catalog</h1><p class="request" id="request-token">${escapeHtml(token)}</p><output class="stamp" id="render-stamp">${Date.now()}</output>${counter}<table><thead><tr><th>ID</th><th>Name</th><th>Price</th><th>Stock</th><th>Note</th></tr></thead><tbody id="catalog-rows">${rows}</tbody><tfoot><tr><td colspan="5" id="checksum">${checksum}</td></tr></tfoot></table></main>\n${interactive ? `<script type="module" src="${JS}"></script>` : ""}</body></html>\n`;
}

const ROUTES: Record<string, [number, boolean]> = { "/": [3, false], "/large": [1000, false], "/interactive": [50, true] };

Bun.serve({
  port: Number(process.argv[2] ?? 3000),
  fetch(req) {
    const url = new URL(req.url);
    const route = ROUTES[url.pathname];
    if (!route) return new Response("Not Found", { status: 404 });
    const q = url.searchParams;
    const n = number(q, "n", route[0], 1, 1000);
    const seed = number(q, "seed", 17, 0, 999);
    const t = q.get("token");
    const token = t !== null && t.length <= 128 ? t : "fixture";
    return new Response(render(n, seed, token, route[1]), { headers: { "Content-Type": "text/html; charset=utf-8" } });
  },
});
