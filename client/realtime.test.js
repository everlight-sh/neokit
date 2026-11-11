import {test,expect} from 'bun:test';
import {Window} from 'happy-dom';
import {subscribe} from './realtime.js';
import {validDashboard,mount as mountDashboard} from '../examples/saas/client/live.js';

function fixture(){
  const saved={};for(const key of ['window','document','location','EventSource'])saved[key]=globalThis[key];
  const window=new Window({url:'http://localhost/dashboard'});for(const key of ['Error','SyntaxError','TypeError','RangeError'])if(!window[key])window[key]=globalThis[key];globalThis.window=window;globalThis.document=window.document;globalThis.location=window.location;
  let hidden=false;Object.defineProperty(window.document,'hidden',{get:()=>hidden,configurable:true});
  const sources=[];class Source{
    constructor(url){this.url=url;this.listeners=new Map();this.readyState=0;sources.push(this);}
    addEventListener(name,fn){this.listeners.set(name,fn);}
    close(){this.closed=true;this.readyState=2;}
    send(name,data,id='77.1'){this.listeners.get(name)?.({data:typeof data==='string'?data:JSON.stringify(data),lastEventId:id});}
  }globalThis.EventSource=Source;
  return {sources,window,visibility(value){hidden=value;window.document.dispatchEvent(new window.Event('visibilitychange'));},restore(){for(const [key,value] of Object.entries(saved))if(value===undefined)delete globalThis[key];else globalThis[key]=value;window.happyDOM.abort();}};
}
test('subscriptions validate JSON, deduplicate IDs, resume cursors and recover replay gaps',()=>{
  const f=fixture();let subscription;
  try{
    const values=[],errors=[],states=[];let resets=0;
    subscription=subscribe('/events',{events:{metric:{validate:x=>Number.isSafeInteger(x.count),receive:x=>values.push(x.count)}},onError:e=>errors.push(e),onReset:()=>resets++,onStatus:s=>states.push(s)});
    f.sources[0].onopen();f.sources[0].send('metric',{count:1},'77.1');f.sources[0].send('metric',{count:999},'77.1');
    f.sources[0].send('metric','invalid','77.2');f.sources[0].send('metric',{count:'bad'},'77.3');f.sources[0].send('metric',{count:4},'77.4');
    expect(values).toEqual([1,4]);expect(errors.length).toBe(2);expect(subscription.cursor).toBe('77.4');
    subscription.pause();expect(f.sources[0].closed).toBe(true);subscription.resume();expect(new URL(f.sources[1].url).searchParams.get('after')).toBe('77.4');
    f.sources[0].send('metric',{count:500},'77.5');expect(values).toEqual([1,4]);
    f.sources[1].send('neokit-reset',{});f.sources[1].send('metric',{count:1},'88.1');expect(resets).toBe(1);expect(values).toEqual([1,4,1]);
    f.visibility(true);expect(f.sources[1].closed).toBe(true);f.visibility(false);expect(f.sources.length).toBe(3);expect(states).toContain('paused');
    subscription.close();f.visibility(false);expect(f.sources.length).toBe(3);
  }finally{subscription?.close();f.restore();}
});
test('rejects cross-origin subscriptions, oversized payloads and unsafe IDs',()=>{
  const f=fixture();let subscription;
  try{
    const handler={validate:()=>true,receive:()=>{throw new Error('Must not receive invalid payload');}};
    expect(()=>subscribe('https://other.example/events',{events:{x:handler}})).toThrow('same-origin');
    expect(()=>subscribe('/events',{events:{'neokit-reset':handler}})).toThrow();
    const errors=[];subscription=subscribe('/events',{maxBytes:8,events:{x:handler},onError:e=>errors.push(e)});
    f.sources[0].send('x','"123456789"');f.sources[0].send('x','{}','1.9007199254740992');expect(errors.length).toBe(2);expect(subscription.cursor).toBe('');
    expect(validDashboard({tick:1,stamp:2,amount:29,revenue:29,signups:0,kind:'Invoice',company:'Demo'})).toBe(true);
    expect(validDashboard({tick:1,stamp:2,amount:29,revenue:Infinity,signups:0,kind:'Invoice',company:'Demo'})).toBe(false);
  }finally{subscription?.close();f.restore();}
});
test('dashboard retains only 60 chart samples and 12 activity rows, rendering event text safely',()=>{
  const f=fixture();let stop;
  try{
    const root=f.window.document.createElement('section');root.innerHTML='<output data-live-status></output><button data-live-toggle>Pause</button><strong data-live-revenue></strong><strong data-live-signups></strong><strong data-live-count></strong><span data-live-updated></span><span data-live-cursor></span><svg><path data-live-line></path></svg><p data-live-chart-label></p><ol data-live-feed></ol>';
    f.window.document.body.append(root);stop=mountDashboard(root);
    for(let i=1;i<=200;i++)f.sources[0].send('dashboard',{tick:i,stamp:1700000000000+i,amount:29,revenue:i*29,signups:i,kind:'Invoice',company:'<img src=x onerror=bad()>'},`77.${i}`);
    expect(root.querySelector('[data-live-count]').textContent).toBe('200');expect(root.querySelector('[data-live-feed]').children.length).toBe(12);
    expect(root.querySelector('[data-live-line]').getAttribute('d').split('L').length).toBe(60);expect(root.querySelector('img')).toBeNull();
    expect(root.querySelector('[data-live-feed]').textContent).toContain('<img src=x onerror=bad()>');
    root.querySelector('button').click();expect(root.querySelector('[data-live-status]').textContent).toBe('Paused');
  }finally{stop?.();f.restore();}
});
