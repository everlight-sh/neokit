import { describe, expect, test } from "bun:test";
import { formURL, navigationURL, message } from "./runtime.js";
import { safeFragment } from "./stream.js";
import { Window } from "happy-dom";

describe("form status text", () => {
  test("form data attributes override neutral defaults", () => {
    const form = { dataset: { webPending: "Asking…", webSuccess: "" } };
    expect(message(form, "pending", "Loading…")).toBe("Asking…");
    expect(message(form, "success", "Saved.")).toBe("");
    expect(message(form, "failure", "Something went wrong.")).toBe("Something went wrong.");
  });
});

describe("fragment sanitizer", () => {
  test("rejects executable markup and URLs in every URL attribute", () => {
    const window = new Window();
    for (const key of ["Error", "SyntaxError", "TypeError", "RangeError", "RegExp"]) if (!window[key]) window[key] = globalThis[key];
    const parse = html => { const template = window.document.createElement("template"); template.innerHTML = html; return template.content; };
    expect(safeFragment(parse('<p><a href="/ok">ok</a><img src="/x.png"></p>'))).toBe(true);
    for (const html of ['<script>x</script>', '<a href=" javascript:alert(1)">x</a>', '<object data="javascript:alert(1)"></object>',
                        '<video poster="javascript:x"></video>', '<div onclick="x"></div>', '<meta http-equiv="refresh" content="0">']) {
      expect(safeFragment(parse(html))).toBe(false);
    }
    window.happyDOM.abort();
  });
});

describe("browser navigation boundaries", () => {
  test("keeps query and same-origin paths", () => {
    expect(navigationURL("/notes/native?q=a#part", "https://site.test/").href).toBe("https://site.test/notes/native?q=a#part");
  });
  test("rejects external origins and executable protocols", () => {
    for (const target of ["https://other.test/", "javascript:alert(1)", "data:text/html,hello", "//other.test/"]) {
      expect(navigationURL(target, "https://site.test/")).toBeNull();
    }
  });
  test("GET forms encode Unicode and reserved text without duplicating old values", () => {
    const url = formURL("/greet?name=old&fixed=1", [["name", "<script> café & tea"], ["tag", "a"], ["tag", "b"]], "https://site.test/");
    expect(url.searchParams.getAll("name")).toEqual(["<script> café & tea"]);
    expect(url.searchParams.getAll("tag")).toEqual(["a", "b"]);
    expect(url.searchParams.get("fixed")).toBe("1");
    expect(url.href).not.toContain("<script>");
  });
  test("rejects form cross-origin requests and unsupported file uploads", () => {
    expect(() => formURL("https://other.test/", [], "https://site.test/")).toThrow();
    expect(() => formURL("/", [["file", new Blob(["x"])]], "https://site.test/")).toThrow();
  });
});
