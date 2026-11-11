import {keyedList} from '../../../../client/keyed.js';
import {windowedList} from '../../../../client/windowed.js';
import {readData} from '../../../../client/data.js';

export function mount(root) {
  const $=selector=>root.querySelector(selector),body=$('[data-stress-rows]'),viewport=$('[data-stress-viewport]');
  const size=$('[data-stress-size]'),mode=$('[data-stress-mode]'),filter=$('[data-stress-filter]');
  const auto=$('[data-stress-auto]'),verify=$('[data-stress-verify]'),report=$('[data-testid=stress-check-report]'),details=$('[data-testid=stress-metrics]');
  const fullDOM=Boolean($('[data-full-dom]')),rowHeight=64,cache=new WeakMap(),template=body.querySelector('[data-row-id]').cloneNode(true);
  function decode(section){
    const data=readData(section,'stress');
    if(!Number.isSafeInteger(data.count)||data.count<1||data.count>10000||!Array.isArray(data.values)||data.values.length!==data.count||!data.values.every(Number.isSafeInteger))throw new Error('Invalid server list data');
    return {stamp:data.stamp,items:data.values.map((value,index)=>({id:index+1,label:`Item ${index+1}`,value,note:'',selected:false}))};
  }
  let initial=decode(root),items=initial.items,nextId=items.length+1,serverRequest,running=false,timer,frame,stopped=false,busy=false,controller;
  const initialSSRRows=body.querySelectorAll('[data-row-id]').length,times=[],steadyTimes=[],updateTimes=[];
  const metrics={renderer:fullDOM?'full-dom':'windowed',initialSSRRows,serverRefreshes:0,serverStamp:initial.stamp,updates:0,liveTicks:0,blankFrames:0,keyedFocusLosses:0,redrawFocusLosses:0,longFrames:0,worstFrameMs:0,lastCreated:0,lastRemoved:0,lastRetained:initialSSRRows,lastMoved:0,lastPatchMs:0,visibleRows:items.length,totalRows:items.length,mountedRows:initialSSRRows};
  function parts(row){
    if(!cache.has(row))cache.set(row,{idCell:row.firstElementChild,label:row.querySelector('[data-label]'),value:row.querySelector('[data-value]'),note:row.querySelector('[data-note]'),select:row.querySelector('[data-select]'),bump:row.querySelector('[data-bump]')});
    return cache.get(row);
  }
  const rowNodes=()=>[...body.querySelectorAll('[data-row-id]')];
  function create(){return template.cloneNode(true);}
  function update(row,item){
    const p=parts(row),id=String(item.id);row.style.height=`${rowHeight}px`;
    if(p.id!==id){if(p.idCell.textContent!==id)p.idCell.textContent=id;row.dataset.rowId=id;p.note.setAttribute('aria-label',`Note for item ${id}`);p.bump.setAttribute('aria-label',`Increment item ${id}`);p.id=id;}
    if(p.lastLabel!==item.label){p.label.textContent=item.label;p.lastLabel=item.label;}
    if(p.lastValue!==item.value){p.value.textContent=String(item.value);p.lastValue=item.value;}
    if(p.lastNote!==item.note){if(p.note.value!==item.note)p.note.value=item.note;p.lastNote=item.note;}
    if(p.lastSelected!==item.selected){p.select.setAttribute('aria-pressed',String(item.selected));p.lastSelected=item.selected;}
  }
  function adopt(){
    controller?.dispose?.();
    if(fullDOM){
      for(const node of body.querySelectorAll('[data-web-spacer]'))node.remove();
      let patch=keyedList(body,{key:item=>item.id,create,update,readKey:row=>row.dataset.rowId});
      controller={update(rows,{retain=true}={}){if(!retain){body.replaceChildren();patch=keyedList(body,{key:item=>item.id,create,update,readKey:row=>row.dataset.rowId});}return {...patch(rows),mounted:rows.length};},render(){},scrollToIndex(index){viewport.scrollTop=index*rowHeight;}};
    }else controller=windowedList(body,{viewport,rowHeight,key:item=>item.id,create,update,readKey:row=>row.dataset.rowId,
      createSpacer(){const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=6;cell.style.cssText='padding:0;border:0;height:inherit';row.append(cell);return row;},
      onRender(change){metrics.mountedRows=change.mounted;$('[data-stress-window]').textContent=`${change.mounted} mounted rows · ${change.logical.toLocaleString()} matching records · windowed DOM`;}});
  }
  function visible(){const query=filter.value.trim().toLowerCase();return query?items.filter(item=>`${item.label} ${item.note}`.toLowerCase().includes(query)):items;}
  function publish(){
    const percentile=values=>{const sorted=[...values].sort((a,b)=>a-b);return sorted.length?sorted[Math.max(0,Math.ceil(sorted.length*.95)-1)]:0;};
    metrics.p95UpdateMs=percentile(updateTimes);metrics.p95PatchMs=percentile(times);metrics.p95SteadyPatchMs=percentile(steadyTimes);metrics.mode=mode.value;metrics.running=running;metrics.totalRows=items.length;
    $('[data-stress-count]').textContent=`${metrics.visibleRows.toLocaleString()} / ${items.length.toLocaleString()}`;
    $('[data-stress-time]').textContent=`${metrics.lastPatchMs.toFixed(1)}ms / ${metrics.p95SteadyPatchMs.toFixed(1)}ms`;
    $('[data-stress-errors]').textContent=`${metrics.blankFrames} / ${metrics.keyedFocusLosses}`;
    $('[data-stress-churn]').textContent=`${metrics.lastCreated} / ${metrics.lastRemoved}`;
    $('[data-stress-status]').textContent=`${running?'Live updates running':'Paused'} · ${metrics.updates} patches · ${metrics.liveTicks} live ticks · ${metrics.longFrames} frames over 50ms · ${items.filter(item=>item.selected).length} selected`;
    if(fullDOM)$('[data-stress-window]').textContent=`${metrics.mountedRows.toLocaleString()} mounted rows · full DOM diagnostic mode (can stall)`;
    details.textContent=JSON.stringify(metrics,null,2);
  }
  function render(){
    const rows=visible(),active=document.activeElement,activeRow=active?.closest?.('[data-row-id]');
    const preserve=activeRow&&body.contains(activeRow)&&rows.some(item=>String(item.id)===activeRow.dataset.rowId);
    const started=performance.now();if(!controller)adopt();
    const change=controller.update(rows,{retain:mode.value!=='replace'});
    if(preserve&&document.activeElement!==active)metrics[mode.value==='keyed'?'keyedFocusLosses':'redrawFocusLosses']++;
    metrics.lastPatchMs=performance.now()-started;times.push(metrics.lastPatchMs);if(times.length>300)times.shift();
    if(mode.value==='keyed'&&!change.created&&!change.removed&&!change.moved){steadyTimes.push(metrics.lastPatchMs);if(steadyTimes.length>300)steadyTimes.shift();}
    Object.assign(metrics,{updates:metrics.updates+1,visibleRows:rows.length,mountedRows:change.mounted,lastCreated:change.created,lastRemoved:change.removed,lastRetained:change.retained,lastMoved:change.moved});
    body.closest('table').setAttribute('aria-rowcount',String(rows.length+1));$('[data-stress-empty]').hidden=rows.length!==0;$('[data-stress-warning]').hidden=mode.value!=='replace';publish();
  }
  function tick(all=false){const started=performance.now();items=items.map((item,index)=>({...item,value:all||index%10===metrics.liveTicks%10?item.value+1:item.value}));render();updateTimes.push(performance.now()-started);if(updateTimes.length>300)updateTimes.shift();}
  function schedule(){timer=setTimeout(()=>{if(stopped||!running)return;metrics.liveTicks++;tick();schedule();},100);}
  function setRunning(value){running=value;clearTimeout(timer);auto.textContent=value?'Pause live updates':'Start live updates';if(value)schedule();publish();}
  function makeItem(){const id=nextId++;return {id,label:`Item ${id}`,value:id*17%1000,note:'',selected:false};}
  async function refreshFromServer(){
    serverRequest?.abort();const abort=new AbortController();serverRequest=abort;
    try{
      const url=new URL(location.pathname,location.origin);url.searchParams.set('n',String(items.length));
      if(fullDOM)url.searchParams.set('render','full');
      const response=await fetch(url,{signal:abort.signal,headers:{'X-Web-Fragment':'1',Accept:'text/html'},cache:'no-store'});
      if(!response.ok)throw new Error('Server refresh failed');
      const page=new DOMParser().parseFromString(await response.text(),'text/html'),fresh=decode(page);
      if(stopped||abort.signal.aborted)return;
      const local=new Map(items.map(item=>[item.id,item]));
      items=fresh.items.map(item=>({...item,note:local.get(item.id)?.note||'',selected:local.get(item.id)?.selected||false}));
      nextId=Math.max(nextId,items.length+1);metrics.serverRefreshes++;metrics.serverStamp=fresh.stamp;render();
    }catch(error){if(error.name!=='AbortError'){$('[data-stress-status]').textContent=String(error);throw error;}}
  }
  function onClick(event){
    if(busy)return;const button=event.target.closest('button');if(!button||!root.contains(button))return;
    const row=button.closest('[data-row-id]');
    if(row){const item=items.find(item=>item.id===Number(row.dataset.rowId));if(!item)return;if(button.hasAttribute('data-select'))item.selected=!item.selected;if(button.hasAttribute('data-bump'))item.value++;render();return;}
    if(button===auto)setRunning(!running);else if(button===verify)runVerification();
    else if(button.hasAttribute('data-stress-refresh'))refreshFromServer().catch(()=>{});
    else if(button.hasAttribute('data-stress-update'))tick(true);
    else if(button.hasAttribute('data-stress-sort')){items=[...items].reverse();render();}
    else if(button.hasAttribute('data-stress-shuffle')){items=[...items];for(let i=items.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[items[i],items[j]]=[items[j],items[i]];}render();}
    else if(button.hasAttribute('data-stress-add')){items=[...items,...Array.from({length:Math.min(100,10000-items.length)},makeItem)];render();}
    else if(button.hasAttribute('data-stress-remove')){items=items.slice(0,Math.max(1,items.length-100));render();}
  }
  function onInput(event){if(busy)return;if(event.target===filter){render();return;}if(event.target.matches('[data-note]')){const item=items.find(item=>item.id===Number(event.target.closest('[data-row-id]').dataset.rowId));if(item)item.note=event.target.value;}}
  function onChange(event){if(busy)return;if(event.target===mode)render();if(event.target===size){const count=Number(size.value);items=Array.from({length:count},(_,i)=>items[i]||makeItem());filter.value='';render();}}
  let previousFrame,lastPublish=0;
  function watchFrame(now){if(stopped)return;if(document.visibilityState==='visible'){if(metrics.visibleRows>0&&!body.querySelector('[data-row-id]'))metrics.blankFrames++;if(previousFrame!==undefined){const gap=now-previousFrame;metrics.worstFrameMs=Math.max(metrics.worstFrameMs,gap);if(gap>50)metrics.longFrames++;}previousFrame=now;}else previousFrame=undefined;if(now-lastPublish>500){publish();lastPublish=now;}frame=requestAnimationFrame(watchFrame);}
  async function runVerification(){
    busy=true;setRunning(false);const saved=items,savedFilter=filter.value,checks=[];
    const controls=[...root.querySelectorAll('[data-stress-auto],[data-stress-update],[data-stress-shuffle],[data-stress-refresh],[data-stress-sort],[data-stress-add],[data-stress-remove],[data-stress-verify],select,[data-stress-filter]')];for(const control of controls)control.disabled=true;
    verify.textContent='Running checks…';report.textContent='Running real DOM checks…';
    const check=(name,passed)=>checks.push({name,passed:Boolean(passed)});
    try{
      items=items.map(item=>({...item}));filter.value='';mode.value='keyed';render();
      const before=new Map(rowNodes().map(row=>[row.dataset.rowId,row])),first=items[0];
      controller.scrollToIndex(0);const row=body.querySelector(`[data-row-id="${first.id}"]`),note=parts(row).note;
      first.note='Draft <safe> & café stays put';first.selected=true;render();note.focus({preventScroll:true});note.setSelectionRange(6,12);
      const losses=metrics.keyedFocusLosses;
      for(let i=0;i<24;i++){if(stopped)throw new DOMException('Widget disposed','AbortError');tick(true);await new Promise(resolve=>requestAnimationFrame(resolve));}
      check('Generated-array updates preserve mounted row nodes',rowNodes().every(row=>!before.has(row.dataset.rowId)||before.get(row.dataset.rowId)===row));
      check('Updates preserve focus, draft and caret',document.activeElement===note&&note.value===first.note&&note.selectionStart===6&&note.selectionEnd===12&&metrics.keyedFocusLosses===losses);
      items=[...items].reverse();render();check('Reordering preserves focused node and selection',document.activeElement===note&&row.isConnected&&note.selectionStart===6&&parts(row).select.getAttribute('aria-pressed')==='true');
      filter.value=first.label;render();check('Filtering preserves the matching focused row',row.isConnected&&document.activeElement===note&&note.value===first.note);
      filter.value='';render();
      const existing=new Map(rowNodes().map(row=>[row.dataset.rowId,row]));items=[...items,...Array.from({length:100},makeItem)];render();items=items.slice(0,-100);render();
      check('Add/remove preserves surviving mounted row nodes',rowNodes().every(row=>!existing.has(row.dataset.rowId)||existing.get(row.dataset.rowId)===row));
      controller.scrollToIndex(0);controller.scrollToIndex(items.length-1);
      check('The last logical record can be rendered',Boolean(body.querySelector(`[data-row-id="${items.at(-1).id}"]`)));
      check('Scrolling pins the focused editor',row.isConnected&&document.activeElement===note&&note.selectionStart===6);
      if(!fullDOM)check('DOM row count stays bounded',rowNodes().length<=64);
      if($('[data-stress-refresh]')){const stamp=metrics.serverStamp;await refreshFromServer();check('Fresh native data preserves focused input and local state',metrics.serverStamp>stamp&&document.activeElement===note&&row.isConnected&&note.value===first.note&&parts(row).select.getAttribute('aria-pressed')==='true');}
      note.focus({preventScroll:true});mode.value='replace';render();check('Negative control detects visible-row redraw and focus loss',!row.isConnected&&document.activeElement!==note&&metrics.redrawFocusLosses>0);
      mode.value='keyed';items=saved;filter.value=savedFilter;render();
      check('Rendered IDs match stable row keys',rowNodes().every(row=>row.firstElementChild.textContent===row.dataset.rowId));
      filter.value='__no_matching_record__';render();check('Empty filters render the empty state without stale rows',rowNodes().length===0&&!$('[data-stress-empty]').hidden);filter.value=savedFilter;render();
      check('No unexplained empty rendered frames',metrics.blankFrames===0);
      report.textContent=JSON.stringify({passed:checks.every(c=>c.passed),rows:items.length,renderer:metrics.renderer,mountedRows:metrics.mountedRows,checks,keyedFocusLosses:metrics.keyedFocusLosses,expectedRedrawFocusLosses:metrics.redrawFocusLosses,blankFrames:metrics.blankFrames,serverRefreshes:metrics.serverRefreshes,p95UpdateMs:metrics.p95UpdateMs,p95SteadyPatchMs:metrics.p95SteadyPatchMs,p95AllOperationsMs:metrics.p95PatchMs,worstFrameMs:metrics.worstFrameMs},null,2);
    }catch(error){report.textContent=JSON.stringify({passed:false,error:String(error),checks},null,2);}
    finally{busy=false;if(!stopped){mode.value='keyed';items=saved;filter.value=savedFilter;render();for(const control of controls)if(control.isConnected)control.disabled=false;verify.textContent='Run verification';}}
  }
  size.value=String(items.length);adopt();render();metrics.mountReadyMs=performance.now();root.addEventListener('click',onClick);root.addEventListener('input',onInput);root.addEventListener('change',onChange);frame=requestAnimationFrame(watchFrame);
  return()=>{stopped=true;controller?.dispose?.();serverRequest?.abort();clearTimeout(timer);cancelAnimationFrame(frame);root.removeEventListener('click',onClick);root.removeEventListener('input',onInput);root.removeEventListener('change',onChange);};
}
