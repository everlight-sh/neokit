import { test, expect } from 'bun:test';
import { Window } from 'happy-dom';
import { start } from './runtime.js';
import { clearState } from './state.js';

async function until(check){for(let i=0;i<100;i++){if(check())return;await new Promise(resolve=>setTimeout(resolve,2));}throw new Error('DOM update did not finish');}

test('navigation preserves a shared layout, widget state, cleanup and prefetch',async()=>{
  const window=new Window({url:'https://app.test/one',settings:{disableJavaScriptFileLoading:true,disableCSSFileLoading:true}});
  const subscriptions=[];
  window.EventSource=class{constructor(url){this.url=url;this.readyState=0;subscriptions.push(this);}addEventListener(){}close(){this.closed=true;}};
  for(const key of ['Error','SyntaxError','TypeError','RangeError'])if(!window[key])window[key]=globalThis[key];
  const names=['window','document','Element','HTMLFormElement','DOMParser','FormData','CustomEvent','location','CSS','scrollX','scrollY','fetch','EventSource'];
  const previous=new Map(names.map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)]));
  const restore=()=>{for(const [name,descriptor] of previous){if(descriptor)Object.defineProperty(globalThis,name,descriptor);else delete globalThis[name];}};
  for(const name of names)if(name!=='fetch')Object.defineProperty(globalThis,name,{configurable:true,writable:true,value:name==='window'?window:name==='scrollX'||name==='scrollY'?0:window[name]});
  window.scrollTo=()=>{};
  const markup=(name)=>`<!doctype html><html><head><title>${name}</title><script type="module" src="/app.js"></script></head><body><main id="web-page" tabindex="-1"><div data-web-layout="dashboard"><aside data-web-persist="sidebar"><input id="draft" value="original"></aside><div data-web-outlet="dashboard"><h1>${name}</h1><div data-web-widget="counter" data-web-key="runtime-test:counter" data-web-props='{ "initial": 3 }'><output></output><button>Add</button></div><div data-web-widget="broken"></div><a href="/two" data-web-link data-web-prefetch>Two</a></div></div></main></body></html>`;
  window.document.write(markup('One'));
  let mounted=0,disposed=0,calls=0;
  globalThis.fetch=async()=>{calls++;return new Response(markup('Two'),{headers:{'Content-Type':'text/html','Cache-Control':'public, max-age=60'}});};
  clearState('runtime-test:');
  const originalConsoleError=console.error;const errors=[];console.error=(...args)=>errors.push(args);
  const subscriptionOptions={pauseWhenHidden:false,events:{notice:{validate:()=>true,receive:()=>{}}}};
  const dispose=start({widgets:{counter:async()=>({mount(element,context){
    context.subscribe('/events/good',subscriptionOptions);
    mounted++;const count=context.state('count',context.props.initial);
    const stop=count.subscribe(value=>element.querySelector('output').textContent=String(value),{immediate:true});
    const button=element.querySelector('button');const click=()=>count.set(value=>value+1);button.addEventListener('click',click);
    return()=>{disposed++;stop();button.removeEventListener('click',click);};
  }}),broken:async()=>({mount(element,context){context.subscribe('/events/broken',subscriptionOptions);throw new Error('Expected widget mount failure');}})}});
  try{
    await until(()=>mounted===1);
    await until(()=>window.document.querySelector('[data-web-widget="broken"]').dataset.webError==='true');
    expect(subscriptions.find(source=>source.url.includes('broken')).closed).toBe(true);
    const sidebar=window.document.querySelector('aside'),draft=window.document.getElementById('draft');draft.value='retained draft';
    window.document.querySelector('button').click();expect(window.document.querySelector('output').textContent).toBe('4');
    const link=window.document.querySelector('a');link.dispatchEvent(new window.MouseEvent('pointerover',{bubbles:true}));
    await until(()=>calls===1);await new Promise(resolve=>setTimeout(resolve,5));
    link.dispatchEvent(new window.MouseEvent('click',{button:0,bubbles:true,cancelable:true}));
    await until(()=>window.document.title==='Two' && mounted===2);
    expect(calls).toBe(1);expect(disposed).toBe(1);
    expect(subscriptions.filter(source=>source.url.includes('good'))[0].closed).toBe(true);
    expect(window.document.querySelector('aside')).toBe(sidebar);expect(window.document.getElementById('draft').value).toBe('retained draft');
    expect(window.document.querySelector('output').textContent).toBe('4');
    // A streamed document needs a full navigation even when its module entry
    // matches. Buffered replacement would lose templates outside #web-page.
    window.document.dispatchEvent(new window.CustomEvent('web:mutation'));
    let redirected;
    globalThis.location={href:window.location.href,assign:url=>{redirected=String(url);}};
    globalThis.fetch=async()=>new Response(markup('Stream shell').replace('<html>','<html data-web-streaming>'),{headers:{'Content-Type':'text/html'}});
    window.document.querySelector('a').dispatchEvent(new window.MouseEvent('click',{button:0,bubbles:true,cancelable:true}));
    await until(()=>redirected);
    expect(redirected).toBe('https://app.test/two');expect(window.document.title).toBe('Two');expect(mounted).toBe(2);
    dispose();expect(disposed).toBe(2);
    expect(subscriptions.every(source=>source.closed)).toBe(true);expect(errors.length).toBe(2);
  }finally{dispose();console.error=originalConsoleError;clearState('runtime-test:');await window.happyDOM.abort();restore();}
});
