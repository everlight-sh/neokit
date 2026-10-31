// Preserve DOM nodes by stable key, including input state and event handlers.
export function keyedList(parent, { key, create, update, readKey }) {
  let nodes = new Map();
  for (const node of parent.children) {
    const id = String(readKey(node));
    if (nodes.has(id)) throw new Error(`Duplicate existing list key: ${id}`);
    nodes.set(id, node);
  }
  let order = [...nodes.keys()];
  return (items) => {
    const keys = items.map((item) => String(key(item)));
    const wanted = new Set(keys);
    if (wanted.size !== keys.length) throw new Error("Duplicate list keys");
    if (keys.length === order.length && keys.every((id,index) => id === order[index])) {
      for (let i=0;i<items.length;i++) update(nodes.get(keys[i]),items[i]);
      return { created:0, removed:0, moved:0, retained:items.length };
    }
    const active = parent.ownerDocument.activeElement;
    const preserve = active && parent.contains(active);
    const start = preserve ? active.selectionStart : null;
    const end = preserve ? active.selectionEnd : null;
    const direction = preserve ? active.selectionDirection : null;
    let created = 0, removed = 0, moved = 0, retained = 0;
    for (const [id, node] of nodes) {
      if (!wanted.has(id)) { node.remove(); nodes.delete(id); removed++; }
    }
    let cursor = parent.firstElementChild;
    for (let i = 0; i < items.length; i++) {
      const id = keys[i];
      let node = nodes.get(id);
      if (!node) { node = create(items[i]); nodes.set(id, node); created++; }
      else retained++;
      update(node, items[i]);
      if (node === cursor) cursor = cursor.nextElementSibling;
      else {
        if (node.parentNode === parent && typeof parent.moveBefore === "function") parent.moveBefore(node, cursor);
        else parent.insertBefore(node, cursor);
        moved++;
      }
    }
    // Older browsers blur a focused descendant on insertBefore moves.
    if (preserve && active.isConnected && parent.contains(active)) {
      if (parent.ownerDocument.activeElement !== active) active.focus({ preventScroll: true });
      if (start !== null && typeof active.setSelectionRange === "function" &&
          (active.selectionStart !== start || active.selectionEnd !== end || active.selectionDirection !== direction))
        active.setSelectionRange(start, end, direction);
    }
    order = keys;
    return { created, removed, moved, retained };
  };
}
