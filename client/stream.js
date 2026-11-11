// Deferred HTML is inert until its separate ready marker arrives. The parser
// may expose a <template> before its closing tag, so observing it alone is unsafe.
const sessions = new WeakMap();
const idPattern = /^[A-Za-z0-9_-]{1,64}$/;

export function safeFragment(fragment) {
  if (fragment.querySelector('script,iframe,object,embed,base,style,link,meta')) return false;
  for (const element of fragment.querySelectorAll('*')) {
    for (const attribute of element.attributes) {
      const name = attribute.name.toLowerCase();
      if (name.startsWith('on') || name === 'srcdoc') return false;
      if (/^(href|src|action|formaction|xlink:href)$/.test(name) &&
          /^(javascript|vbscript|data):/i.test(attribute.value.replace(/[\u0000-\u0020]/g, ''))) return false;
    }
  }
  return true;
}

export function startStreaming({ document: doc = globalThis.document, beforeReplace, afterReplace } = {}) {
  if (!doc) return () => {};
  let session = sessions.get(doc);
  if (!session) {
    const listeners = new Set();
    const process = () => {
      for (const marker of doc.querySelectorAll('template[data-web-ready]')) {
        const key = marker.dataset.webReady;
        const split = key.indexOf(':');
        const token = key.slice(0, split), id = key.slice(split + 1);
        const root = [...doc.querySelectorAll('[data-web-stream]')].find(node => node.dataset.webStream === token);
        const payload = [...doc.querySelectorAll('template[data-web-fragment]')].find(node => node.dataset.webFragment === key);
        const target = root && [...root.querySelectorAll('[data-web-boundary]')].find(node => node.dataset.webBoundary === id);
        if (split < 1 || !idPattern.test(token) || !idPattern.test(id) || !payload || !target || target.dataset.webState !== 'pending') {
          payload?.remove(); marker.remove(); continue;
        }
        const valid = safeFragment(payload.content);
        for (const listener of listeners) listener.beforeReplace?.(target);
        if (valid) target.replaceChildren(payload.content);
        else target.textContent = 'Could not load this section.';
        target.dataset.webState = valid && payload.dataset.webState === 'ready' ? 'ready' : 'error';
        target.removeAttribute('aria-busy');
        target.dataset.webStreamedMs = String(doc.defaultView.performance.now());
        payload.remove(); marker.remove();
        for (const listener of listeners) listener.afterReplace?.(target);
        doc.dispatchEvent(new doc.defaultView.CustomEvent('web:streamed', { detail: { id, target, state: target.dataset.webState } }));
      }
      for (const marker of doc.querySelectorAll('template[data-web-stream-end]')) {
        const token = marker.dataset.webStreamEnd;
        const root = [...doc.querySelectorAll('[data-web-stream]')].find(node => node.dataset.webStream === token);
        finish(root); marker.remove();
      }
    };
    const finish = root => {
      if (!root) return;
      root.removeAttribute('aria-busy');
      for (const target of root.querySelectorAll('[data-web-boundary][data-web-state="pending"]')) {
        target.dataset.webState = 'error'; target.removeAttribute('aria-busy');
        target.textContent = 'Could not load this section.';
      }
    };
    const loaded = () => { process(); for (const root of doc.querySelectorAll('[data-web-stream]')) finish(root); };
    const observer = new doc.defaultView.MutationObserver(records => {
      if (records.some(record => [...record.addedNodes].some(node => node.nodeType === 1 &&
        (node.matches?.('template[data-web-ready],template[data-web-stream-end]') || node.querySelector?.('template[data-web-ready],template[data-web-stream-end]'))))) process();
    });
    observer.observe(doc, { childList: true, subtree: true });
    doc.addEventListener('DOMContentLoaded', loaded, { once: true });
    session = { listeners, process: () => { if (doc.readyState === 'loading') process(); else loaded(); }, dispose() { observer.disconnect(); doc.removeEventListener('DOMContentLoaded', loaded); sessions.delete(doc); } };
    sessions.set(doc, session);
  }
  const listener = { beforeReplace, afterReplace };
  session.listeners.add(listener);
  session.process();
  return () => { session.listeners.delete(listener); if (!session.listeners.size) session.dispose(); };
}
