import { describe, expect, test } from "bun:test";
import { formURL, navigationURL } from "./runtime.js";

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
