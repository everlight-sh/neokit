// Independent oracle and HTML reader for tests/catalog_fixture; excludes
// framework boot scripts. The reader follows html.parser.HTMLParser
// (convert_charrefs=True, fed once and never closed) and records the
// benchmark element's attributes, the catalog rows, the token/stamp/checksum
// values, counter widgets, scripts, stylesheets and inline scripts.
import { expect } from "bun:test";
import { request, sleep, strictInt, urlencode } from "../lib";

export const WORDS = ["Widget", "Gadget", "Gizmo", "Doohickey", "Sprocket", "Flange", "Bracket", "Grommet"];
export const ADJECTIVES = ["Deluxe", "Basic", "Pro", "Mini", "Ultra", "Classic", "Eco", "Max"];
export const SPECIAL = "Ben & Jerry's \"Special\" <limited> café";
export const SCENARIOS = [
  { name: "small-ssr", path: "/", n: 3, interactive: false },
  { name: "large-ssr", path: "/large", n: 1000, interactive: false },
  { name: "interactive-ssr", path: "/interactive", n: 50, interactive: true },
];

export function rows(n: number, seed: number): string[][] {
  return Array.from({ length: n }, (_, i) => [String(i), WORDS[(i + seed) % 8] + " " + ADJECTIVES[(i * 7 + seed) % 8], String((i * 37 + seed * 13) % 1000) + ".99", String((i * 13 + seed) % 50), i % 5 === 0 ? SPECIAL : "-"]);
}

// html.unescape(). Numeric references are decoded in full: the replacement
// table, U+FFFD for surrogates and values past U+10FFFF, nothing for other
// invalid code points. Named references are resolved from the table below;
// one that the full HTML5 table might resolve differently is an error rather
// than a guess.
const INVALID_CHARREFS: Record<number, string> = {
  0x00: "�", 0x0d: "\r", 0x80: "€", 0x81: "\x81", 0x82: "‚", 0x83: "ƒ", 0x84: "„", 0x85: "…", 0x86: "†", 0x87: "‡",
  0x88: "ˆ", 0x89: "‰", 0x8a: "Š", 0x8b: "‹", 0x8c: "Œ", 0x8d: "\x8d", 0x8e: "Ž", 0x8f: "\x8f", 0x90: "\x90", 0x91: "‘",
  0x92: "’", 0x93: "“", 0x94: "”", 0x95: "•", 0x96: "–", 0x97: "—", 0x98: "˜", 0x99: "™", 0x9a: "š", 0x9b: "›",
  0x9c: "œ", 0x9d: "\x9d", 0x9e: "ž", 0x9f: "Ÿ",
};
const invalidCodepoint = (n: number) => (n >= 0x1 && n <= 0x8) || (n >= 0xe && n <= 0x1f) || (n >= 0x7f && n <= 0x9f) || (n >= 0xfdd0 && n <= 0xfdef) || n === 0xb || (n & 0xfffe) === 0xfffe;
const NAMED: Record<string, string> = {
  "amp;": "&", amp: "&", "AMP;": "&", AMP: "&", "lt;": "<", lt: "<", "LT;": "<", LT: "<", "gt;": ">", gt: ">", "GT;": ">", GT: ">",
  "quot;": '"', quot: '"', "QUOT;": '"', QUOT: '"', "apos;": "'", "nbsp;": "\xa0", nbsp: "\xa0",
};
const CHARREF = /&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)/g;

function replaceCharref(reference: string): string {
  if (reference[0] === "#") {
    const number = reference[1] === "x" || reference[1] === "X" ? parseInt(reference.slice(2).replace(/;$/, ""), 16) : parseInt(reference.slice(1).replace(/;$/, ""), 10);
    if (number in INVALID_CHARREFS) return INVALID_CHARREFS[number];
    if ((number >= 0xd800 && number <= 0xdfff) || number > 0x10ffff) return "�";
    if (invalidCodepoint(number)) return "";
    return String.fromCodePoint(number);
  }
  if (Object.hasOwn(NAMED, reference)) return NAMED[reference];
  // Every HTML5 name is a letter then letters/digits, with an optional final ';'.
  // Full resolution tries the whole reference, then prefixes of two or more characters.
  const run = /^[A-Za-z][A-Za-z0-9]*/.exec(reference)?.[0] ?? "";
  if (run.length < 2 && !/^[A-Za-z];$/.test(reference)) return "&" + reference;
  throw new Error(`character reference &${reference} is outside the oracle's entity table`);
}
export function unescape(text: string) {
  return text.includes("&") ? text.replace(CHARREF, (_, reference) => replaceCharref(reference)) : text;
}

// html.parser's patterns, with the HTML5 tag-name/attribute rules.
const TAGFIND = /([a-zA-Z][^\t\n\r\f />]*)(?:[\t\n\r\f ]|\/(?!>))*/y;
const ATTRFIND = /((?<=['"\t\n\r\f /])[^\t\n\r\f />][^\t\n\r\f /=>]*)([\t\n\r\f ]*=[\t\n\r\f ]*('[^']*'|"[^"]*"|(?!['"])[^>\t\n\r\f ]*))?(?:[\t\n\r\f ]|\/(?!>))*/y;
const LOCATETAGEND = /[a-zA-Z][^\t\n\r\f />]*[\t\n\r\f /]*(?:(?<=['"\t\n\r\f /])[^\t\n\r\f />][^\t\n\r\f /=>]*(?:[\t\n\r\f ]*=[\t\n\r\f ]*(?:'[^']*'|"[^"]*"|(?!['"])[^>\t\n\r\f ]*))?[\t\n\r\f /]*)*>?/y;
const STARTTAGOPEN = /<[a-zA-Z]/y;
const ENDTAGOPEN = /<\/[a-zA-Z]/y;
const COMMENTCLOSE = /--!?>/g;
const COMMENTABRUPTCLOSE = /-?>/y;
const CDATA_CONTENT = ["script", "style", "xmp", "iframe", "noembed", "noframes"];
const RCDATA_CONTENT = ["textarea", "title"];
// Unicode whitespace: JavaScript's \s without U+FEFF, plus U+001C-U+001F and U+0085.
const SPACE_CLASS = "(?:(?!\\ufeff)[\\s\\x1c-\\x1f\\x85])";
const SPACE = new RegExp(`^${SPACE_CLASS}+|${SPACE_CLASS}+$`, "g");
const stripSpace = (text: string) => text.replace(SPACE, "");

function at(pattern: RegExp, text: string, index: number) { pattern.lastIndex = index; return pattern.exec(text); }

type Attributes = [string, string | null][];
interface Handler { start(tag: string, attributes: Attributes): void; end(tag: string): void; data(text: string): void }

/** HTMLParser(convert_charrefs=True).feed(text) without close(): an unterminated construct at the end is dropped. */
export function parseHtml(text: string, handler: Handler) {
  const n = text.length;
  let cdata: string | null = null, escapable = true, cdataEnd: RegExp | null = null;
  const setCdata = (tag: string, canEscape: boolean) => { cdata = tag; escapable = canEscape; cdataEnd = new RegExp(`</${tag}(?=[\\t\\n\\r\\f />])`, "gi"); };
  const clearCdata = () => { cdata = null; escapable = true; cdataEnd = null; };
  const bogusComment = (i: number) => { const close = text.indexOf(">", i + 2); return close < 0 ? -1 : close + 1; };
  const comment = (i: number) => {
    COMMENTCLOSE.lastIndex = i + 4;
    const close = COMMENTCLOSE.exec(text) ?? at(COMMENTABRUPTCLOSE, text, i + 4);
    return close ? close.index + close[0].length : -1;
  };
  const declaration = (i: number) => {
    if (text.startsWith("<!--", i)) return comment(i);
    if (text.startsWith("<![CDATA[", i)) { const close = text.indexOf("]]>", i + 9); return close < 0 ? -1 : close + 3; }
    if (text.slice(i, i + 9).toLowerCase() === "<!doctype") { const close = text.indexOf(">", i + 9); return close < 0 ? -1 : close + 1; }
    if (text.startsWith("<![", i)) { const close = text.indexOf(">", i + 3); return close < 0 ? -1 : close + 1; }
    return bogusComment(i);
  };
  const startTag = (i: number) => {
    const located = at(LOCATETAGEND, text, i + 1)!;
    const endpos = located.index + located[0].length;
    if (text[endpos - 1] !== ">") return -1;
    const name = at(TAGFIND, text, i + 1)!;
    let k = name.index + name[0].length;
    const tag = name[1].toLowerCase();
    const attributes: Attributes = [];
    while (k < endpos) {
      const found = at(ATTRFIND, text, k);
      if (!found) break;
      let value: string | null = found[3] ?? null;
      if (!found[2]) value = null;
      else if (value!.length >= 2 && ((value![0] === "'" && value!.endsWith("'")) || (value![0] === '"' && value!.endsWith('"')))) value = value!.slice(1, -1);
      else if (value === "'" || value === '"') value = "";
      if (value) value = unescape(value);
      attributes.push([found[1].toLowerCase(), value]);
      k = found.index + found[0].length;
    }
    const end = stripSpace(text.slice(k, endpos));
    if (end !== ">" && end !== "/>") { handler.data(text.slice(i, endpos)); return endpos; }
    if (end.endsWith("/>")) { handler.start(tag, attributes); handler.end(tag); }
    else {
      handler.start(tag, attributes);
      if (CDATA_CONTENT.includes(tag) || tag === "plaintext") setCdata(tag, false);
      else if (RCDATA_CONTENT.includes(tag)) setCdata(tag, true);
    }
    return endpos;
  };
  const endTag = (i: number) => {
    if (text.indexOf(">", i + 2) < 0) return -1;
    if (!at(ENDTAGOPEN, text, i)) return text[i + 2] === ">" ? i + 3 : bogusComment(i);
    const located = at(LOCATETAGEND, text, i + 2)!;
    const j = located.index + located[0].length;
    if (text[j - 1] !== ">") return -1;
    handler.end(at(TAGFIND, text, i + 2)![1].toLowerCase());
    clearCdata();
    return j;
  };

  let i = 0;
  while (i < n) {
    let j: number;
    if (!cdata) {
      j = text.indexOf("<", i);
      if (j < 0) {
        // A reference may be cut at the end of the buffer: wait for more text.
        const amp = text.lastIndexOf("&");
        if (amp >= Math.max(i, n - 34) && !/[\t\n\r\f ;]/.test(text.slice(amp))) break;
        j = n;
      }
    } else if (cdata === "plaintext") j = n;
    else {
      cdataEnd!.lastIndex = i;
      const found = cdataEnd!.exec(text);
      if (!found) break;
      j = found.index;
    }
    if (i < j) handler.data(escapable ? unescape(text.slice(i, j)) : text.slice(i, j));
    i = j;
    if (i === n) break;
    let k: number;
    if (at(STARTTAGOPEN, text, i)) k = startTag(i);
    else if (text.startsWith("</", i)) k = endTag(i);
    else if (text.startsWith("<!--", i)) k = comment(i);
    else if (text.startsWith("<?", i)) { const close = text.indexOf(">", i + 2); k = close < 0 ? -1 : close + 1; }
    else if (text.startsWith("<!", i)) k = declaration(i);
    else if (i + 1 < n) { handler.data("<"); k = i + 1; }
    else break;
    if (k < 0) break;
    i = k;
  }
}

const strictUtf8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
const VALUE_IDS = ["request-token", "render-stamp", "checksum"] as const;
type ValueId = typeof VALUE_IDS[number];

/** The catalog page reader. */
export class Catalog implements Handler {
  attrs: Record<string, string | null> = {};
  rows: string[][] = [];
  values: Record<ValueId, string> = { "request-token": "", "render-stamp": "", checksum: "" };
  scripts: string[] = [];
  styles: string[] = [];
  inlineScripts: Buffer[] = [];
  counters = 0;
  private row: string[] | null = null;
  private cell: string[] | null = null;
  private inRows = false;
  private capture: [string, ValueId] | null = null;
  private script: string[] | null = null;

  constructor(body: Buffer) { parseHtml(strictUtf8.decode(body), this); }

  start(tag: string, attributes: Attributes) {
    const attrs: Record<string, string | null> = {};
    for (const [name, value] of attributes) attrs[name] = value;
    const get = (name: string) => (Object.hasOwn(attrs, name) ? attrs[name] : undefined);
    if (tag === "main" && get("id") === "benchmark") this.attrs = attrs;
    if (tag === "tbody" && get("id") === "catalog-rows") this.inRows = true;
    if (this.inRows && tag === "tr") this.row = [];
    if (this.inRows && tag === "td") this.cell = [];
    const id = get("id");
    if (typeof id === "string" && (VALUE_IDS as readonly string[]).includes(id)) this.capture = [tag, id as ValueId];
    if (get("data-web-widget") === "counter") this.counters++;
    if (tag === "script") {
      this.script = [];
      if (get("src")) this.scripts.push(get("src")!);
    }
    if (tag === "link" && get("href")) {
      if (get("rel") === "stylesheet") this.styles.push(get("href")!);
      if (get("rel") === "modulepreload" || (get("rel") === "preload" && get("as") === "script")) this.scripts.push(get("href")!);
    }
  }

  data(text: string) {
    if (this.script !== null) { this.script.push(text); return; }
    if (this.cell !== null) this.cell.push(text);
    if (this.capture) this.values[this.capture[1]] += text;
  }

  end(tag: string) {
    if (tag === "script" && this.script !== null) { this.inlineScripts.push(Buffer.from(this.script.join(""))); this.script = null; }
    if (tag === "td" && this.cell !== null) {
      if (this.row === null) throw new Error("AttributeError: a catalog cell outside a row");
      this.row.push(this.cell.join(""));
      this.cell = null;
    }
    if (tag === "tr" && this.inRows && this.row !== null) { this.rows.push(this.row); this.row = null; }
    if (tag === "tbody") this.inRows = false;
    if (this.capture && tag === this.capture[0]) this.capture = null;
  }
}

/** Check one catalog page against the oracle; returns the parsed page. */
export function validate(body: Buffer, n: number, seed: number, token: string, interactive: boolean, isStatic = false): Catalog {
  const page = new Catalog(body);
  expect([page.attrs["data-n"], page.attrs["data-seed"]]).toEqual([String(n), String(seed)]);
  expect(page.values["request-token"]).toBe(token);
  const expected = rows(n, seed);
  const same = (a: string[], b: string[]) => a.length === b.length && a.every((cell, index) => cell === b[index]);
  let firstDifference: [number, string[], string[]] | null = null;
  for (let i = 0; i < Math.min(page.rows.length, expected.length) && !firstDifference; i++)
    if (!same(page.rows[i], expected[i])) firstDifference = [i, page.rows[i], expected[i]];
  expect([page.rows.length, firstDifference]).toEqual([expected.length, null]);
  const checksum = expected.reduce((total, row) => total + strictInt(row[2].slice(0, -3)) + strictInt(row[3]), 0);
  expect(strictInt(page.values.checksum)).toBe(checksum);
  expect(page.counters).toBe(Number(interactive));
  const stamp = strictInt(page.values["render-stamp"]);
  if (isStatic) expect(stamp).toBe(0);
  else expect(Math.abs(stamp - Date.now())).toBeLessThan(60000);
  return page;
}

/** One GET with a 30 s timeout: status, the reply's headers, body, time to first byte and total (ms). */
export async function get(port: number, path: string) {
  const reply = await request(port, path, { headers: { "Accept-Encoding": "identity" }, timeout: 30000, keepAlive: true });
  return { status: reply.status, headers: reply, body: reply.body, ttfb: reply.ttfb, total: reply.total };
}

/** Every scenario with three parameter sets, then a freshness check; returns the pages checked. */
export async function verifyServer(port: number) {
  let checked = 0;
  for (const scenario of SCENARIOS) {
    for (const [n, seed, token] of [[scenario.n, 17, "fixture"], [1, 999, '<script>"café" & tea'], [17, 2, "changed-token"]] as [number, number, string][]) {
      const path = scenario.path + "?" + urlencode({ n, seed, token });
      const { status, headers, body } = await get(port, path);
      expect([path, status, (headers.last("Content-Type") ?? "").includes("text/html")]).toEqual([path, 200, true]);
      expect([scenario.name, (headers.last("Cache-Control") ?? "").includes("no-store")]).toEqual([scenario.name, true]);
      validate(body, n, seed, token, scenario.interactive);
      checked++;
    }
  }
  const first = new Catalog((await get(port, "/?token=freshness")).body);
  await sleep(30);
  const second = new Catalog((await get(port, "/?token=freshness")).body);
  expect(strictInt(second.values["render-stamp"])).toBeGreaterThan(strictInt(first.values["render-stamp"]));
  return checked;
}
