import { defineConfig } from "astro/config";
import node from "@astrojs/node";

const isStatic = process.env.BENCH_MODE === "static";
export default defineConfig({
  output: isStatic ? "static" : "server",
  outDir: isStatic ? "./dist-static" : "./dist-ssr",
  adapter: isStatic ? undefined : node({ mode: "standalone" }),
  session: false,
  compressHTML: true,
  cacheDir: "./.astro",
});
