import { start } from "../../../client/runtime.js";

start({ widgets: {
  counter: () => import("./widgets/counter.js"),
  filter: () => import("./widgets/filter.js"),
  "list-stress": () => import("./widgets/list-stress.js"),
} });
