import { start } from "./../../../client/runtime.js";
start({widgets:{counter:()=>import("./counter.js")}});
