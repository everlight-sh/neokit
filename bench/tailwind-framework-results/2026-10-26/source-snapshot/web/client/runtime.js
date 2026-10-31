// Browser-only framework code. The Axon server serves these built modules.
export function navigationURL(href, base) {
  const url = new URL(href, base);
  const origin = new URL(base);
  return /^(https?:)$/.test(url.protocol) && url.origin === origin.origin ? url : null;
}

export function formURL(action, entries, base) {
  const url = navigationURL(action, base);
  if (!url) throw new Error("Forms must use a same-origin HTTP URL");
  const seen = new Set();
  for (const [key, value] of entries) {
    if (typeof value !== "string") throw new Error("File uploads are outside the v0.1 form profile");
    if (!seen.has(key)) url.searchParams.delete(key);
    seen.add(key);
    url.searchParams.append(key, value);
  }
  return url;
}

export function start({ widgets = {} } = {}) {
  const mounted = new WeakMap();
  const forms = new WeakMap();
  let navigation;
  let stopped = false;
  const entry = document.querySelector('script[type="module"][src]')?.src;
  const nodes = (root, selector) => [
    ...(root instanceof Element && root.matches(selector) ? [root] : []),
    ...root.querySelectorAll(selector),
  ];

  async function mount(root) {
    await Promise.all(nodes(root, "[data-web-widget]").map(async (element) => {
      if (mounted.has(element)) return;
      const load = widgets[element.dataset.webWidget];
      if (!load) return;
      const instance = { dispose: null };
      mounted.set(element, instance);
      try {
        const module = await load();
        if (stopped || !element.isConnected || mounted.get(element) !== instance) return;
        instance.dispose = module.mount(element);
        element.dataset.webReady = "true";
      } catch (error) {
        mounted.delete(element);
        element.dataset.webError = "true";
        console.error("Axon Web widget failed", error);
      }
    }));
  }

  function unmount(root) {
    for (const element of nodes(root, "[data-web-widget]")) {
      mounted.get(element)?.dispose?.();
      mounted.delete(element);
    }
    for (const form of nodes(root, "form[data-web-form]")) forms.get(form)?.abort();
  }

  function markNavigation() {
    const current = location.pathname.replace(/\/$/, "") || "/";
    for (const link of document.querySelectorAll("nav a[data-web-link]")) {
      const path = new URL(link.href).pathname;
      if (path === current || path === "/notes/native" && current.startsWith("/notes/")) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
  }

  async function navigate(url, history = true) {
    navigation?.abort();
    const controller = new AbortController();
    navigation = controller;
    document.documentElement.dataset.webNavigating = "true";
    try {
      const response = await fetch(url, { signal: controller.signal, headers: { Accept: "text/html" } });
      if (!response.ok || !response.headers.get("content-type")?.includes("text/html")) throw new Error("Full navigation required");
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const next = page.querySelector("#web-page");
      const current = document.querySelector("#web-page");
      const nextEntry = page.querySelector('script[type="module"][src]')?.getAttribute("src");
      if (!next || !current || nextEntry && new URL(nextEntry, url).href !== entry) throw new Error("Different application build");
      if (controller.signal.aborted) return;
      unmount(current);
      current.replaceChildren(...next.childNodes);
      document.title = page.title;
      document.body.dataset.renderMode = page.body.dataset.renderMode || "";
      const label = document.querySelector(".render-label");
      const nextLabel = page.querySelector(".render-label");
      if (label && nextLabel) label.textContent = nextLabel.textContent;
      if (history) window.history.pushState({}, "", url);
      markNavigation();
      await mount(current);
      current.focus({ preventScroll: true });
      window.scrollTo(0, 0);
      document.dispatchEvent(new CustomEvent("web:navigated", { detail: { url: String(url) } }));
    } catch (error) {
      if (error.name !== "AbortError") location.assign(url);
    } finally {
      if (navigation === controller) delete document.documentElement.dataset.webNavigating;
    }
  }

  function onClick(event) {
    const link = event.target.closest?.("a[data-web-link]");
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.hasAttribute("download") || link.target) return;
    const url = navigationURL(link.href, location.href);
    if (!url || url.hash && url.pathname === location.pathname && url.search === location.search) return;
    event.preventDefault();
    navigate(url);
  }

  async function onSubmit(event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.matches("[data-web-form]")) return;
    const selector = form.dataset.webTarget || "";
    if (form.method.toLowerCase() !== "get" || !/^#[A-Za-z][\w-]*$/.test(selector)) return;
    const target = document.querySelector(selector);
    if (!target) return;
    let url;
    try { url = formURL(form.action, new FormData(form, event.submitter).entries(), location.href); }
    catch { return; }
    event.preventDefault();
    forms.get(form)?.abort();
    const controller = new AbortController();
    forms.set(form, controller);
    form.setAttribute("aria-busy", "true");
    const status = form.querySelector("[data-web-status]");
    if (status) status.textContent = "Asking the server…";
    try {
      const response = await fetch(url, { signal: controller.signal, headers: { "X-Web-Fragment": "1", Accept: "text/html" } });
      if (!response.ok || !response.headers.get("content-type")?.includes("text/html")) throw new Error("The server could not render this response.");
      const template = document.createElement("template");
      template.innerHTML = await response.text();
      if (template.content.querySelector("script, iframe, object, embed, base, style, link")) throw new Error("Unexpected executable fragment.");
      for (const element of template.content.querySelectorAll("*")) {
        for (const attribute of element.attributes) {
          if (/^on/i.test(attribute.name) || attribute.name === "srcdoc" || /^(href|src|action|formaction)$/i.test(attribute.name) && /^\s*(javascript|vbscript|data):/i.test(attribute.value)) throw new Error("Unexpected executable fragment attribute.");
        }
      }
      if (controller.signal.aborted || forms.get(form) !== controller || !target.isConnected) return;
      unmount(target);
      target.replaceChildren(template.content);
      await mount(target);
      if (status) status.textContent = "Fresh HTML, rendered by Axon.";
      document.dispatchEvent(new CustomEvent("web:updated", { detail: { target } }));
    } catch (error) {
      if (error.name !== "AbortError" && status) status.textContent = "Couldn't reach the server. Please try again.";
    } finally {
      if (forms.get(form) === controller) form.removeAttribute("aria-busy");
    }
  }

  const onPop = () => navigate(new URL(location.href), false);
  document.addEventListener("click", onClick);
  document.addEventListener("submit", onSubmit);
  window.addEventListener("popstate", onPop);
  markNavigation();
  mount(document);
  return () => {
    stopped = true;
    navigation?.abort();
    unmount(document);
    document.removeEventListener("click", onClick);
    document.removeEventListener("submit", onSubmit);
    window.removeEventListener("popstate", onPop);
  };
}
