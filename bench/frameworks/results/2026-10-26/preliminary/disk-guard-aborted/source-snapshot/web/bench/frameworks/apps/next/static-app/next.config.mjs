import path from "node:path";
export default {
  output: "export",
  trailingSlash: true,
  outputFileTracingRoot: path.resolve(import.meta.dirname, "../../.."),
  turbopack: { root: path.resolve(import.meta.dirname, "../../..") },
};
