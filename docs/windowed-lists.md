# Large lists and inert page data

`client/windowed.js` exports a reusable fixed-height renderer. It keeps models
for the whole dataset while mounting the viewport plus overscan and, when
needed, one focused editor. `update` validates unique stable keys before
mutation, preserves the scroll anchor across model changes, and reuses mounted
nodes through `keyedList`. Offscreen rows are recreated from model-owned state.

```js
import {windowedList} from '/path/to/neokit/client/windowed.js';
const list=windowedList(container,{
  viewport, rowHeight:64, overscan:6,
  key:item=>item.id,
  readKey:node=>node.dataset.id,
  create:item=>createRow(item),
  update:(node,item)=>updateRow(node,item),
});
list.update(models);
list.scrollToIndex(models.length-1);
// On component cleanup:
list.dispose();
```

Use an explicitly sized, scrollable viewport and genuinely fixed row heights.
Tables supply `createSpacer` returning an inert `<tr>` with a spanning cell.
Variable-height rows, pagination, remote data and screen-reader testing beyond
row metadata remain application concerns. Windowing changes browser find and
printing behavior; offer a full rendering or export when every row must exist
in the DOM. Resize observation refreshes the window after viewport growth or
empty-list transitions. Focused rows are pinned only while they still match the dataset.
Store drafts/selection in models through input events, not solely in DOM nodes.

Native `std:http_data` provides `web_data(name, json)` for escaped inert page
data. Its `<template data-web-data>` contains JSON text, with no inline script
or hidden row tree. `readData(root,name)` in `client/data.js` decodes it; optional
`remove` and `maxLength` control lifetime and bounds. Validate the decoded
application schema before use. Values containing `</template>` remain text.

The studio `/stress?n=10000` sends 32 SSR rows plus data for all records, then
mounts about 15–22 rows in its 520px viewport. It keeps a deliberately expensive
`render=full` diagnostic mode and a no-JavaScript full-render link. Static export
uses the same renderer and model behavior.

Actual-browser before/after results in the `neokit-bench` repository
separate the bounded-window workload from the saved unvirtualized reference.
