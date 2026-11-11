// One EventSource multiplexes named, schema-checked JSON events. It can be used
// directly or through context.subscribe(), which disposes it with its widget.
export function subscribe(href, {events, onStatus=()=>{}, onReset=()=>{}, onError=()=>{}, pauseWhenHidden=true, maxBytes=8192}={}) {
  const url=new URL(href,location.href);
  if(!['http:','https:'].includes(url.protocol)||url.origin!==location.origin)throw new Error('Realtime requires a same-origin HTTP URL');
  if(!events||Object.keys(events).length<1||Object.keys(events).length>16||!Number.isInteger(maxBytes)||maxBytes<1||maxBytes>65536)throw new Error('Invalid subscription bounds');
  for(const [name,handler] of Object.entries(events))if(!/^[\w.:-]{1,48}$/.test(name)||name.startsWith('neokit-')||typeof handler.validate!=='function'||typeof handler.receive!=='function')throw new Error('Events require named validators and receivers');
  let source,closed=false,paused=false,cursor='',generation=0,retryTimer,backoff=1000;
  const report=error=>onError(error instanceof Error?error:new Error(String(error)));
  const close=()=>{generation++;clearTimeout(retryTimer);source?.close();source=null;};
  const status=value=>onStatus(value);
  const connect=()=>{
    if(closed||paused||pauseWhenHidden&&document.hidden)return;
    const current=++generation;const target=new URL(url);if(cursor)target.searchParams.set('after',cursor);
    status('connecting');source=new EventSource(target.href);
    source.onopen=()=>{if(current===generation){backoff=1000;status('live');}};
    source.onerror=()=>{if(current!==generation)return;status('reconnecting');if(source.readyState===2){close();retryTimer=setTimeout(connect,backoff);backoff=Math.min(30000,backoff*2);}};
    source.addEventListener('neokit-reset',()=>{if(current!==generation)return;cursor='';onReset();});
    for(const [name,handler] of Object.entries(events))source.addEventListener(name,event=>{
      if(current!==generation||closed)return;
      try{
        if(typeof event.data!=='string'||new TextEncoder().encode(event.data).length>maxBytes)throw new Error('Realtime payload exceeds limit');
        const match=/^(\d{1,19})\.(\d{1,16})$/.exec(event.lastEventId||'');
        if(!match||!Number.isSafeInteger(Number(match[2])))throw new Error('Invalid realtime event ID');
        const before=/^(\d+)\.(\d+)$/.exec(cursor);
        if(before&&before[1]===match[1]&&Number(match[2])<=Number(before[2]))return;
        const value=JSON.parse(event.data);if(!handler.validate(value))throw new Error(`Invalid ${name} event`);
        handler.receive(value,{id:event.lastEventId,event:name});cursor=event.lastEventId;
      }catch(error){report(error);}
    });
  };
  const visibility=()=>{if(!pauseWhenHidden||closed)return;if(document.hidden){close();status('paused');}else if(!paused)connect();};
  const pagehide=()=>{close();};const pageshow=()=>{if(!closed&&!source)connect();};
  document.addEventListener('visibilitychange',visibility);window.addEventListener('pagehide',pagehide);window.addEventListener('pageshow',pageshow);
  if(pauseWhenHidden&&document.hidden)status('paused');else connect();
  return {
    pause(){if(closed||paused)return;paused=true;close();status('paused');},
    resume(){if(closed||!paused)return;paused=false;connect();},
    close(){if(closed)return;closed=true;close();document.removeEventListener('visibilitychange',visibility);window.removeEventListener('pagehide',pagehide);window.removeEventListener('pageshow',pageshow);status('closed');},
    get cursor(){return cursor;}
  };
}
