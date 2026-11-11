// Small observable state primitives, shared by widgets and hot module updates.
const stores = globalThis[Symbol.for('neokit.state')] ||= new Map();
export function signal(initial) {
  let value=initial;
  const listeners=new Set();
  return {
    get:()=>value,
    set(next) {
      const updated=typeof next==='function'?next(value):next;
      if (Object.is(updated,value)) return value;
      value=updated;
      for (const listener of [...listeners]) listener(value);
      return value;
    },
    subscribe(listener,{immediate=false}={}) {
      listeners.add(listener);
      if (immediate) listener(value);
      return ()=>listeners.delete(listener);
    },
  };
}
export function state(key,initial) {
  if (!key || typeof key!=='string') throw new TypeError('State needs a stable string key');
  if (!stores.has(key)) stores.set(key,signal(typeof initial==='function'?initial():initial));
  return stores.get(key);
}
export function computed(sources,derive) {
  const result=signal(derive(...sources.map(source=>source.get())));
  const unsubscribes=sources.map(source=>source.subscribe(()=>result.set(derive(...sources.map(s=>s.get())))));
  return {...result,dispose:()=>unsubscribes.forEach(unsubscribe=>unsubscribe())};
}
export function clearState(prefix='') {
  for (const key of stores.keys()) if (key.startsWith(prefix)) stores.delete(key);
}
export function createAction(url,{csrf,fetch:transport=globalThis.fetch,base=globalThis.location?.href}={}) {
  const endpoint=new URL(url,base);
  if (endpoint.origin!==new URL(base).origin || !/^https?:$/.test(endpoint.protocol)) throw new Error('Actions must use a same-origin HTTP URL');
  const pending=signal(false),error=signal(null);
  return {pending,error,async run(data,{method='POST',optimistic}={}) {
    if (pending.get()) throw new Error('Action already pending');
    if (!['POST','PUT','PATCH','DELETE'].includes(method)) throw new Error('Unsupported mutation method');
    pending.set(true);error.set(null);
    let rollback;
    try {
      rollback=optimistic?.(data);
      const response=await transport(endpoint,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':typeof csrf==='function'?csrf():csrf||''},body:JSON.stringify(data)});
      const type=response.headers.get('content-type')||'';
      const value=response.status===204?null:type.includes('application/json')?await response.json():await response.text();
      if (!response.ok) throw Object.assign(new Error(value?.error||`Action failed (${response.status})`),{status:response.status,value});
      globalThis.document?.dispatchEvent(new CustomEvent('web:mutation',{detail:{url:String(endpoint)}}));
      return value;
    } catch (failure) {
      rollback?.();error.set(failure);throw failure;
    } finally {pending.set(false);}
  }};
}
