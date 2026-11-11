import { startStreaming } from "./stream.js";
import { state, createAction } from "./state.js";
import { replaceContent, syncMetadata } from "./dom.js";
import { subscribe } from "./realtime.js";
// Browser-only framework code. The Neo server serves these built modules.
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
  const entry = document.documentElement.dataset.webEntry || document.querySelector('script[type="module"][src]')?.src;
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
        const key=element.dataset.webKey || element.id || location.pathname+':'+element.dataset.webWidget+':'+nodes(document,'[data-web-widget]').indexOf(element);
        const props=element.dataset.webProps?JSON.parse(element.dataset.webProps):{};
        const cleanups=[];
        instance.dispose=()=>{for(const fn of cleanups)fn();};
        const context={props,state:(name,initial)=>state(key+':'+name,initial),action:(url,options)=>createAction(url,options),cleanup:fn=>cleanups.push(fn),subscribe:(url,options)=>{const subscription=subscribe(url,options);cleanups.push(()=>subscription.close());return subscription;}};
        const cleanup=module.mount(element,context);
        instance.dispose=()=>{if(typeof cleanup==='function')cleanup();for(const fn of cleanups)fn();};
        element.dataset.webReady = "true";
      } catch (error) {
        instance.dispose?.();
        mounted.delete(element);
        element.dataset.webError = "true";
        console.error("NeoKit widget failed", error);
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
      const cached=prefetched.get(String(url));
      const response=cached && cached.expires>Date.now()?new Response(cached.text,{headers:{'content-type':'text/html'}}):await fetch(url, { signal: controller.signal, headers: { Accept: "text/html" } });
      if (!response.ok || !response.headers.get("content-type")?.includes("text/html")) throw new Error("Full navigation required");
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      // Streaming needs the browser's incremental parser. The buffered router
      // would otherwise drop fragment templates outside #web-page.
      if (page.documentElement.hasAttribute('data-web-streaming')) throw new Error("Streaming document requires full navigation");
      const next = page.querySelector("#web-page");
      const current = document.querySelector("#web-page");
      const nextEntry = page.querySelector('script[type="module"][src]')?.getAttribute("src");
      if (!next || !current || nextEntry && new URL(nextEntry, url).href !== entry) throw new Error("Different application build");
      if (controller.signal.aborted) return;
      const oldWidgets=nodes(current,'[data-web-widget]');
      const oldForms=nodes(current,'form[data-web-form]');
      replaceContent(current,next);
      for (const element of oldWidgets) if (!element.isConnected) {mounted.get(element)?.dispose?.();mounted.delete(element);}
      for (const form of oldForms) if (!form.isConnected) forms.get(form)?.abort();
      syncMetadata(document,page);
      document.title = page.title;
      document.body.dataset.renderMode = page.body.dataset.renderMode || "";
      const label = document.querySelector(".render-label");
      const nextLabel = page.querySelector(".render-label");
      if (label && nextLabel) label.textContent = nextLabel.textContent;
      if (history) {
        window.history.replaceState({...window.history.state,webScroll:[scrollX,scrollY]},'',location.href);
        window.history.pushState({}, "", url);
      }
      markNavigation();
      await mount(current);
      current.focus({ preventScroll: true });
      const scroll=history?[0,0]:window.history.state?.webScroll||[0,0];
      window.scrollTo(...scroll);
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
    if (form.method.toLowerCase()!=='get') {
      if (form.method.toLowerCase()!=='post' || !navigationURL(form.action,location.href)) return;
      event.preventDefault();
      if (forms.has(form)) return;
      const controller=new AbortController();forms.set(form,controller);form.setAttribute('aria-busy','true');
      const status=form.querySelector('[data-web-status]');
      if(status)status.textContent='Saving…';
      try {
        const entries=new FormData(form,event.submitter);
        const files=[...entries.values()].some(value=>typeof value!=='string');
        const response=await fetch(form.action,{method:'POST',body:files?entries:new URLSearchParams(entries),credentials:'same-origin',signal:controller.signal});
        if(!response.ok)throw new Error(await response.text());
        document.dispatchEvent(new CustomEvent('web:mutation'));
        if(response.redirected)await navigate(new URL(response.url));
        else if(status)status.textContent='Saved.';
      } catch(error) {if(error.name!=='AbortError' && status)status.textContent=error.message;}
      finally {forms.delete(form);form.removeAttribute('aria-busy');}
      return;
    }
    if (!/^#[A-Za-z][\w-]*$/.test(selector)) return;
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
      if (status) status.textContent = "Fresh HTML, rendered by Neo.";
      document.dispatchEvent(new CustomEvent("web:updated", { detail: { target } }));
    } catch (error) {
      if (error.name !== "AbortError" && status) status.textContent = "Couldn't reach the server. Please try again.";
    } finally {
      if (forms.get(form) === controller) form.removeAttribute("aria-busy");
    }
  }

  const prefetched=new Map();
  let prefetchController;
  async function prefetch(link) {
    const url=navigationURL(link.href,location.href);
    if(!url || prefetched.has(String(url)) || link.hasAttribute('data-web-no-prefetch'))return;
    prefetchController=new AbortController();
    try {
      const response=await fetch(url,{signal:prefetchController.signal,credentials:'same-origin'});
      const control=response.headers.get('cache-control')||'';
      const age=Number(/max-age=(\d+)/.exec(control)?.[1]||0);
      if(response.ok && control.includes('public') && age>0 && response.headers.get('content-type')?.includes('text/html')) {
        if(prefetched.size>=20)prefetched.delete(prefetched.keys().next().value);
        prefetched.set(String(url),{text:await response.text(),expires:Date.now()+Math.min(age*1000,30000)});
      }
    } catch {}
  }
  const onHover=event=>{const link=event.target.closest?.('a[data-web-link][data-web-prefetch]');if(link)prefetch(link);};
  const invalidate=()=>prefetched.clear();
  document.addEventListener('pointerover',onHover);
  document.addEventListener('web:mutation',invalidate);
  const onPop = () => navigate(new URL(location.href), false);
  document.addEventListener("click", onClick);
  document.addEventListener("submit", onSubmit);
  window.addEventListener("popstate", onPop);
  const stopStreaming = document.documentElement.hasAttribute('data-web-streaming') || document.querySelector('[data-web-stream]')
    ? startStreaming({ beforeReplace: unmount, afterReplace: target => mount(target) }) : () => {};
  markNavigation();
  mount(document);
  const dispose = () => {
    stopped = true;
    navigation?.abort();prefetchController?.abort();stopStreaming();
    document.removeEventListener('pointerover',onHover);document.removeEventListener('web:mutation',invalidate);
    unmount(document);
    document.removeEventListener("click", onClick);
    document.removeEventListener("submit", onSubmit);
    window.removeEventListener("popstate", onPop);
  };
  globalThis.__NEOKIT_DISPOSE__=dispose;
  return dispose;
}
