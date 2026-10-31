// Use the same production dependency tracer shipped in Next.js to package Astro.
// Build-time only; the measured Astro server is the unchanged official output.
import { createRequire } from "node:module";
import fs from "node:fs/promises";
import path from "node:path";

const require=createRequire(import.meta.url);
const {nodeFileTrace}=require("next/dist/compiled/@vercel/nft");
const [base,entry,destination]=process.argv.slice(2).map(x=>path.resolve(x));
const traced=await nodeFileTrace([entry],{base,processCwd:path.dirname(entry)});
for(const name of traced.fileList){
  const source=path.join(base,name),target=path.join(destination,name);
  await fs.mkdir(path.dirname(target),{recursive:true});
  await fs.copyFile(source,target);
}
await fs.writeFile(path.join(destination,"trace.json"),JSON.stringify({files:[...traced.fileList].sort(),warnings:[...traced.warnings].map(String)},null,2));
