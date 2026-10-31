export const WORDS = ["Widget", "Gadget", "Gizmo", "Doohickey", "Sprocket", "Flange", "Bracket", "Grommet"];
export const ADJECTIVES = ["Deluxe", "Basic", "Pro", "Mini", "Ultra", "Classic", "Eco", "Max"];
export const SPECIAL = 'Ben & Jerry\'s "Special" <limited> café';
function number(value, fallback, minimum, maximum) {
  return typeof value === "string" && /^\d{1,4}$/.test(value) && Number(value) >= minimum && Number(value) <= maximum ? Number(value) : fallback;
}
export function data(params, defaultN, stamp) {
  const get = (key) => params instanceof URLSearchParams ? params.get(key) : Array.isArray(params[key]) ? params[key][0] : params[key];
  const n = number(get("n"), defaultN, 1, 1000);
  const seed = number(get("seed"), 17, 0, 999);
  const rawToken = get("token");
  const token = typeof rawToken === "string" && new TextEncoder().encode(rawToken).length <= 128 ? rawToken : "fixture";
  const rows = Array.from({ length: n }, (_, i) => ({
    id: i,
    name: `${WORDS[(i + seed) % 8]} ${ADJECTIVES[(i * 7 + seed) % 8]}`,
    price: `${(i * 37 + seed * 13) % 1000}.99`,
    stock: (i * 13 + seed) % 50,
    note: i % 5 === 0 ? SPECIAL : "-",
  }));
  return { n, seed, token, stamp, rows, checksum: rows.reduce((sum, row) => sum + row.stock + Number(row.price.slice(0, -3)), 0) };
}
