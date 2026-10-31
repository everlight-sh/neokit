import { defineConfig } from "astro/config";
import tailwind from "@tailwindcss/vite";
import node from "@astrojs/node";

const isStatic = process.env.BENCH_MODE === "static";
export default defineConfig({
  output: isStatic ? "static" : "server",
  outDir: isStatic ? "./dist-static" : "./dist-ssr",
  adapter: isStatic ? undefined : node({ mode: "standalone" }),
  session: false,
  vite: { plugins: [tailwind()] },
  compressHTML: true,
  cacheDir: "./.astro",
});
