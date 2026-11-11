// Development-only diagnostics and live updates. Never embedded in release builds.
let revision;
let overlay;
let polling=false;
const fieldKey=element=>element.id || element.name;
function snapshot() {
  const fields=[...document.querySelectorAll('input,textarea,select')].filter(element=>fieldKey(element) && !['password','hidden','file'].includes(element.type));
  sessionStorage.setItem('neokit.dev.fields',JSON.stringify(fields.map(element=>[fieldKey(element),element.type==='checkbox'?element.checked:element.value])));
}
try {
  const saved=JSON.parse(sessionStorage.getItem('neokit.dev.fields')||'[]');
  sessionStorage.removeItem('neokit.dev.fields');
  for(const [key,value] of saved){const element=document.getElementById(key)||[...document.querySelectorAll('[name]')].find(node=>node.name===key);if(element){if(element.type==='checkbox')element.checked=value;else element.value=value;}}
} catch {}
async function tick() {
  if(polling)return;polling=true;
  try {
    const response=await fetch('/_neokit/dev',{cache:'no-store'});
    if(!response.ok)return;
    const status=await response.json();
    if(status.error){
      if(!overlay){overlay=document.createElement('pre');overlay.id='neokit-dev-error';overlay.setAttribute('role','alert');document.body.append(overlay);}
      overlay.textContent=status.error;
    } else {overlay?.remove();overlay=null;}
    if(revision!==undefined && revision!==status.revision && !status.error){
      // Development assets keep stable URLs, so a query string busts the browser's copy.
      if(status.kind==='css' || status.kind==='client'){
        for(const link of document.querySelectorAll('link[rel="stylesheet"]')){
          const url=new URL(link.href,location.href);
          if(url.origin!==location.origin || !url.pathname.startsWith('/_web/'))continue;
          url.searchParams.set('dev',status.revision);
          const next=link.cloneNode();next.href=url.href;
          next.addEventListener('load',()=>link.remove(),{once:true});
          next.addEventListener('error',()=>next.remove(),{once:true});
          link.after(next);
        }
      }
      if(status.kind==='client' && status.entry){
        document.documentElement.dataset.webEntry=new URL(status.entry,location.href).href;
        globalThis.__NEOKIT_DISPOSE__?.();
        await import(status.entry+'?dev='+encodeURIComponent(status.revision));
      } else if(status.kind!=='css'){snapshot();location.reload();}
    }
    revision=status.revision;
  } catch {} finally {polling=false;}
}
setInterval(tick,300);tick();
