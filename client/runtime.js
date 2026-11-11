import { startStreaming, safeFragment } from "./stream.js";
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

// Status text for data-web-form: data-web-pending, data-web-success and
// data-web-failure on the form override the neutral defaults.
export function message(form, kind, fallback) {
  const value = form.dataset["web" + kind[0].toUpperCase() + kind.slice(1)];
  return value === undefined ? fallback : value;
}

function withoutHash(url) {
  const copy = new URL(url);
  copy.hash = "";
  return String(copy);
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
      // data-web-load="visible" or "idle" defers loading the widget module
      // without holding up navigation, which awaits eager widgets only.
      const waiting = lazy(element);
      if (waiting) {
        waiting.then(() => {
          if (stopped || !element.isConnected || mounted.get(element) !== instance) return;
          instance.dispose = null;
          return activate(element, instance, load);
        });
        return;
      }
      await activate(element, instance, load);
    }));
  }

  async function activate(element, instance, load) {
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
  }

  function unmount(root) {
    for (const element of nodes(root, "[data-web-widget]")) {
      mounted.get(element)?.dispose?.();
      mounted.delete(element);
    }
    for (const form of nodes(root, "form[data-web-form]")) forms.get(form)?.abort();
  }

  // A nav link is current on an exact path match. data-web-match="prefix"
  // also marks it for pages below it; data-web-match="/section" names the
  // prefix explicitly when the link points at one page inside the section.
  function markNavigation() {
    const current = location.pathname.replace(/\/$/, "") || "/";
    for (const link of document.querySelectorAll("nav a[data-web-link]")) {
      const path = new URL(link.href).pathname.replace(/\/$/, "") || "/";
      const match = link.dataset.webMatch;
      const prefix = match === "prefix" ? path : match?.startsWith("/") ? match.replace(/\/$/, "") : null;
      const active = path === current || prefix !== null && prefix !== "" && (current === prefix || current.startsWith(prefix + "/"));
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
  }

  function scrollToHash(url) {
    const id = decodeURIComponent(url.hash.slice(1));
    const target = id && (document.getElementById(id) || document.getElementsByName(id)[0]);
    if (target) { target.scrollIntoView(); return true; }
    return false;
  }

  function lazy(element) {
    const mode = element.dataset.webLoad;
    if (mode === "visible" && typeof IntersectionObserver === "function") {
      return new Promise(resolve => {
        const observer = new IntersectionObserver(entries => {
          if (entries.some(entry => entry.isIntersecting)) { observer.disconnect(); resolve(); }
        }, { rootMargin: "200px" });
        observer.observe(element);
        mounted.get(element).dispose = () => observer.disconnect();
      });
    }
    if (mode === "idle") {
      return new Promise(resolve => typeof requestIdleCallback === "function" ? requestIdleCallback(() => resolve(), { timeout: 2000 }) : setTimeout(resolve, 200));
    }
    return null;
  }

  async function navigate(url, history = true) {
    navigation?.abort();
    const controller = new AbortController();
    navigation = controller;
    document.documentElement.dataset.webNavigating = "true";
    try {
      const cached=prefetched.get(withoutHash(url));
      const fresh=cached && cached.text!==null && cached.expires>Date.now();
      const response=fresh?new Response(cached.text,{headers:{'content-type':'text/html'}}):await fetch(url, { signal: controller.signal, headers: { Accept: "text/html" } });
      if (!response.ok || !response.headers.get("content-type")?.includes("text/html")) throw new Error("Full navigation required");
      // Follow server redirects in the address bar, keeping the requested fragment.
      if (!fresh && response.redirected && response.url) {
        const final = navigationURL(response.url, location.href);
        if (!final) throw new Error("Cross-origin redirect");
        if (!final.hash) final.hash = url.hash;
        url = final;
      }
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
      const swap = () => {
        replaceContent(current,next);
        syncMetadata(document,page);
        document.title = page.title;
        document.body.dataset.renderMode = page.body.dataset.renderMode || "";
        // Elements outside #web-page marked data-web-sync="key" take their
        // text from the matching element of the next page.
        for (const element of document.querySelectorAll("[data-web-sync]")) {
          if (current.contains(element)) continue;
          const source = [...page.querySelectorAll("[data-web-sync]")].find(node => node.dataset.webSync === element.dataset.webSync);
          if (source) element.textContent = source.textContent;
        }
      };
      // Opt in with <html data-web-transitions> where the View Transitions API exists.
      if (document.documentElement.hasAttribute("data-web-transitions") && typeof document.startViewTransition === "function") {
        await document.startViewTransition(swap).updateCallbackDone;
      } else swap();
      for (const element of oldWidgets) if (!element.isConnected) {mounted.get(element)?.dispose?.();mounted.delete(element);}
      for (const form of oldForms) if (!form.isConnected) forms.get(form)?.abort();
      if (history) {
        window.history.replaceState({...window.history.state,webScroll:[scrollX,scrollY]},'',location.href);
        window.history.pushState({}, "", url);
      }
      markNavigation();
      await mount(current);
      current.focus({ preventScroll: true });
      if (!(history && url.hash && scrollToHash(url))) {
        const scroll=history?[0,0]:window.history.state?.webScroll||[0,0];
        window.scrollTo(...scroll);
      }
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
      const say=(kind,fallback)=>{if(status)status.textContent=message(form,kind,fallback);};
      say('pending','Saving…');
      try {
        const entries=new FormData(form,event.submitter);
        const files=[...entries.values()].some(value=>typeof value!=='string');
        const response=await fetch(form.action,{method:'POST',body:files?entries:new URLSearchParams(entries),credentials:'same-origin',signal:controller.signal});
        if(!response.ok){
          // Short plain-text errors are written for people; anything else is not shown.
          const plain=response.headers.get('content-type')?.startsWith('text/plain');
          const text=plain?(await response.text()).trim():'';
          throw Object.assign(new Error(text),{shown:plain && text.length>0 && text.length<=200});
        }
        document.dispatchEvent(new CustomEvent('web:mutation'));
        if(response.redirected)await navigate(new URL(response.url));
        else say('success','Saved.');
      } catch(error) {if(error.name!=='AbortError' && status)status.textContent=error.shown?error.message:message(form,'failure','Something went wrong. Please try again.');}
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
    if (status) status.textContent = message(form, "pending", "Loading…");
    try {
      const response = await fetch(url, { signal: controller.signal, headers: { "X-Web-Fragment": "1", Accept: "text/html" } });
      if (!response.ok || !response.headers.get("content-type")?.includes("text/html")) throw new Error("The server could not render this response.");
      const template = document.createElement("template");
      template.innerHTML = await response.text();
      if (!safeFragment(template.content)) throw new Error("Unexpected executable fragment.");
      if (controller.signal.aborted || forms.get(form) !== controller || !target.isConnected) return;
      unmount(target);
      target.replaceChildren(template.content);
      await mount(target);
      if (status) status.textContent = message(form, "success", "");
      document.dispatchEvent(new CustomEvent("web:updated", { detail: { target } }));
    } catch (error) {
      if (error.name !== "AbortError" && status) status.textContent = message(form, "failure", "Something went wrong. Please try again.");
    } finally {
      if (forms.get(form) === controller) form.removeAttribute("aria-busy");
    }
  }

  // Prefetch keeps only pages the server marks publicly cacheable. Pages it
  // cannot keep are remembered briefly so hovering again does not refetch.
  const prefetched=new Map();
  const prefetchControllers=new Set();
  async function prefetch(link) {
    const url=navigationURL(link.href,location.href);
    if(!url || link.hasAttribute('data-web-no-prefetch'))return;
    const key=withoutHash(url);
    const known=prefetched.get(key);
    if(known && (known.text===null && !known.settled || known.expires>Date.now()))return;
    if(prefetched.size>=20)prefetched.delete(prefetched.keys().next().value);
    const entry={text:null,settled:false,expires:Date.now()+30000};
    prefetched.set(key,entry);
    const controller=new AbortController();prefetchControllers.add(controller);
    try {
      const response=await fetch(url,{signal:controller.signal,credentials:'same-origin',headers:{Accept:'text/html'}});
      const control=response.headers.get('cache-control')||'';
      const age=Number(/max-age=(\d+)/.exec(control)?.[1]||0);
      if(response.ok && !response.redirected && /(^|,)\s*public\b/.test(control) && age>0 && response.headers.get('content-type')?.includes('text/html')) {
        entry.text=await response.text();
        entry.expires=Date.now()+Math.min(age*1000,30000);
      } else entry.expires=Date.now()+10000;
    } catch { prefetched.delete(key); }
    finally { entry.settled=true; prefetchControllers.delete(controller); }
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
    navigation?.abort();for(const controller of prefetchControllers)controller.abort();stopStreaming();
    document.removeEventListener('pointerover',onHover);document.removeEventListener('web:mutation',invalidate);
    unmount(document);
    document.removeEventListener("click", onClick);
    document.removeEventListener("submit", onSubmit);
    window.removeEventListener("popstate", onPop);
  };
  globalThis.__NEOKIT_DISPOSE__=dispose;
  return dispose;
}
