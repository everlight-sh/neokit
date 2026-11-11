// Reuse shared layouts and explicitly persistent elements during navigation.
export function replaceContent(current,next) {
  const layouts=new Map([...current.querySelectorAll('[data-web-layout]')].map(node=>[node.dataset.webLayout,node]));
  const persistent=new Map([...current.querySelectorAll('[data-web-persist]')].map(node=>[node.dataset.webPersist,node]));
  function retain(root) {
    for (const placeholder of [...root.querySelectorAll('[data-web-layout]')]) {
      if (!root.contains(placeholder)) continue;
      const existing=layouts.get(placeholder.dataset.webLayout);
      if (!existing) continue;
      const key=placeholder.dataset.webLayout;
      const oldOutlet=[...existing.querySelectorAll('[data-web-outlet]')].find(node=>node.dataset.webOutlet===key);
      const newOutlet=[...placeholder.querySelectorAll('[data-web-outlet]')].find(node=>node.dataset.webOutlet===key);
      if (!oldOutlet || !newOutlet) continue;
      retain(newOutlet);
      oldOutlet.replaceChildren(...newOutlet.childNodes);
      placeholder.replaceWith(existing);
    }
    for (const placeholder of [...root.querySelectorAll('[data-web-persist]')]) {
      const existing=persistent.get(placeholder.dataset.webPersist);
      if (existing && existing!==placeholder) placeholder.replaceWith(existing);
    }
  }
  retain(next);
  current.replaceChildren(...next.childNodes);
}
export function syncMetadata(document,page) {
  // JSON-LD is a data block: a script inserted through the DOM with this type never runs.
  const managed='meta[name="description"],meta[name="robots"],meta[property^="og:"],meta[name^="twitter:"],link[rel="canonical"],script[type="application/ld+json"]';
  for (const node of document.head.querySelectorAll(managed)) node.remove();
  for (const source of page.head.querySelectorAll(managed)) {
    const copy=document.importNode(source,true);copy.dataset.webHead='';document.head.append(copy);
  }
}
