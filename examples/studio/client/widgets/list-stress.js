import { keyedList } from "../../../../client/keyed.js";

export function mount(root) {
  const $ = (selector) => root.querySelector(selector);
  const body = $("[data-stress-rows]"), viewport = $("[data-stress-viewport]");
  const size = $("[data-stress-size]"), mode = $("[data-stress-mode]"), filter = $("[data-stress-filter]");
  const auto = $("[data-stress-auto]"), verify = $("[data-stress-verify]");
  const report = $("[data-testid=stress-check-report]"), details = $("[data-testid=stress-metrics]");
  const template = body.firstElementChild.cloneNode(true), cache = new WeakMap();
  let items = [...body.children].map(row => ({ id: Number(row.dataset.rowId), label: `Item ${row.dataset.rowId}`,
    value: Number(row.querySelector("[data-value]").textContent), note: "", selected: false }));
  let nextId = Math.max(...items.map(item => item.id)) + 1;
  let serverRequest;
  let running = false, timer, frame, stopped = false, busy = false, reconciler;
  const times = [], steadyTimes = [];
  const metrics = { initialSSRRows: items.length, serverRefreshes: 0, serverStamp: Number(root.dataset.ssrStamp), updates: 0, liveTicks: 0, blankFrames: 0, keyedFocusLosses: 0,
    redrawFocusLosses: 0, longFrames: 0, worstFrameMs: 0, layoutShift: 0, lastCreated: 0, lastRemoved: 0,
    lastRetained: items.length, lastMoved: 0, lastPatchMs: 0, listLayoutShift: 0, visibleRows: items.length, totalRows: items.length };
  function parts(row) {
    if (!cache.has(row)) cache.set(row, { idCell: row.firstElementChild, label: row.querySelector("[data-label]"), value: row.querySelector("[data-value]"),
      note: row.querySelector("[data-note]"), select: row.querySelector("[data-select]"), bump: row.querySelector("[data-bump]") });
    return cache.get(row);
  }
  function create() { return template.cloneNode(true); }
  function update(row, item) {
    const p = parts(row), id = String(item.id);
    // Newly generated objects can still have identical fields. Compare cached
    // model values before touching DOM properties, especially editable inputs.
    if(p.id!==id) {
      if(p.idCell.textContent!==id)p.idCell.textContent=id;
      if(row.dataset.rowId!==id){row.dataset.rowId=id;p.note.setAttribute("aria-label",`Note for item ${id}`);p.bump.setAttribute("aria-label",`Increment item ${id}`);}p.id=id;
    }
    if(p.lastLabel!==item.label){if(p.label.textContent!==item.label)p.label.textContent=item.label;p.lastLabel=item.label;}
    if(p.lastValue!==item.value){const value=String(item.value);if(p.value.textContent!==value)p.value.textContent=value;p.lastValue=item.value;}
    if(p.lastNote!==item.note){if(p.note.value!==item.note)p.note.value=item.note;p.lastNote=item.note;}
    if(p.lastSelected!==item.selected){const selected=String(item.selected);if(p.select.getAttribute("aria-pressed")!==selected)p.select.setAttribute("aria-pressed",selected);p.lastSelected=item.selected;}
  }
  function adopt() { reconciler = keyedList(body, { key: item => item.id, create, update, readKey: row => row.dataset.rowId }); }
  function visible() {
    const query = filter.value.trim().toLowerCase();
    return query ? items.filter(item => `${item.label} ${item.note}`.toLowerCase().includes(query)) : items;
  }
  function publish() {
    const sorted = [...times].sort((a,b) => a-b);
    metrics.p95PatchMs = sorted.length ? sorted[Math.max(0, Math.ceil(sorted.length*.95)-1)] : 0;
    const steady = [...steadyTimes].sort((a,b)=>a-b);
    metrics.p95SteadyPatchMs=steady.length?steady[Math.max(0,Math.ceil(steady.length*.95)-1)]:0;
    metrics.mode = mode.value; metrics.running = running; metrics.totalRows = items.length;
    $("[data-stress-count]").textContent = `${metrics.visibleRows.toLocaleString()} / ${items.length.toLocaleString()}`;
    $("[data-stress-time]").textContent = `${metrics.lastPatchMs.toFixed(1)}ms / ${metrics.p95SteadyPatchMs.toFixed(1)}ms`;
    $("[data-stress-errors]").textContent = `${metrics.blankFrames} / ${metrics.keyedFocusLosses}`;
    $("[data-stress-churn]").textContent = `${metrics.lastCreated} / ${metrics.lastRemoved}`;
    $("[data-stress-status]").textContent = `${running ? "Live updates running" : "Paused"} · ${metrics.updates} patches · ${metrics.liveTicks} live ticks · ${metrics.longFrames} frames over 50ms · ${items.filter(item => item.selected).length} selected`;
    details.textContent = JSON.stringify(metrics, null, 2);
  }
  function render() {
    const rows = visible(), active = document.activeElement, activeRow = active?.closest?.("[data-row-id]");
    const shouldKeepFocus = activeRow && body.contains(activeRow) && rows.some(item => String(item.id) === activeRow.dataset.rowId);
    const scroll = viewport.scrollTop, started = performance.now();
    let change;
    if (mode.value === "replace") {
      const fragment = document.createDocumentFragment();
      for (const item of rows) { const row = create(); update(row,item); fragment.append(row); }
      change = { created: rows.length, removed: body.children.length, retained: 0, moved: 0 };
      body.replaceChildren(fragment); reconciler = null;
    } else {
      if (!reconciler) adopt();
      change = reconciler(rows);
    }
    if(change.created || change.removed || change.moved)viewport.scrollTop = scroll;
    if (shouldKeepFocus && document.activeElement !== active) metrics[mode.value === "keyed" ? "keyedFocusLosses" : "redrawFocusLosses"]++;
    metrics.lastPatchMs = performance.now()-started;
    times.push(metrics.lastPatchMs); if(times.length>300)times.shift();
    if(mode.value==="keyed" && !change.created && !change.removed && !change.moved){steadyTimes.push(metrics.lastPatchMs);if(steadyTimes.length>300)steadyTimes.shift();}
    Object.assign(metrics,{ updates:metrics.updates+1,visibleRows:rows.length,lastCreated:change.created,lastRemoved:change.removed,lastRetained:change.retained,lastMoved:change.moved });
    $("[data-stress-empty]").hidden = rows.length !== 0;
    $("[data-stress-warning]").hidden = mode.value !== "replace";
    publish();
  }
  function tick(all=false) {
    // Fresh objects for every item, like a generated array on every render.
    items = items.map((item,index) => ({ ...item, value: all || index%10===metrics.liveTicks%10 ? item.value+1 : item.value }));
    render();
  }
  function schedule() {
    timer = setTimeout(() => { if(stopped || !running)return; metrics.liveTicks++;tick();schedule(); },100);
  }
  function setRunning(value) { running=value;clearTimeout(timer);auto.textContent=value?"Pause live updates":"Start live updates";if(value)schedule();publish(); }
  function makeItem() { const id=nextId++;return {id,label:`Item ${id}`,value:id*17%1000,note:"",selected:false}; }
  async function refreshFromServer() {
    serverRequest?.abort();const controller=new AbortController();serverRequest=controller;
    try {
      const url=new URL(location.pathname,location.origin);url.searchParams.set("n",String(items.length));
      const response=await fetch(url,{signal:controller.signal,headers:{"X-Web-Fragment":"1",Accept:"text/html"},cache:"no-store"});
      if(!response.ok)throw new Error("Server refresh failed");
      const page=new DOMParser().parseFromString(await response.text(),"text/html");
      const section=page.querySelector('[data-web-widget="list-stress"]');
      if(!section)throw new Error("Invalid server response");
      const local=new Map(items.map(item=>[item.id,item]));
      const fresh=[...section.querySelectorAll("[data-stress-rows] > tr")].map(row=>{
        const id=Number(row.dataset.rowId),value=Number(row.querySelector("[data-value]").textContent);
        if(!Number.isSafeInteger(id)||id<1||!Number.isSafeInteger(value))throw new Error("Invalid server row");
        return {id,label:row.querySelector("[data-label]").textContent,value,note:local.get(id)?.note||"",selected:local.get(id)?.selected||false};
      });
      if(stopped||controller.signal.aborted)return;
      items=fresh;nextId=Math.max(nextId,...fresh.map(item=>item.id+1));
      metrics.serverRefreshes++;metrics.serverStamp=Number(section.dataset.ssrStamp);render();
    } catch(error) {
      if(error.name!=="AbortError"){$("[data-stress-status]").textContent=String(error);throw error;}
    }
  }
  function onClick(event) {
    if(busy)return;
    const button=event.target.closest("button");if(!button || !root.contains(button))return;
    const row=button.closest("[data-row-id]");
    if(row) {
      const item=items.find(item=>item.id===Number(row.dataset.rowId));if(!item)return;
      if(button.hasAttribute("data-select"))item.selected=!item.selected;
      if(button.hasAttribute("data-bump"))item.value++;
      render();return;
    }
    if(button===auto)setRunning(!running);
    else if(button===verify)runVerification();
    else if(button.hasAttribute("data-stress-refresh"))refreshFromServer().catch(()=>{});
    else if(button.hasAttribute("data-stress-update"))tick(true);
    else if(button.hasAttribute("data-stress-sort")){items=[...items].reverse();render();}
    else if(button.hasAttribute("data-stress-shuffle")) {
      items=[...items];for(let i=items.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[items[i],items[j]]=[items[j],items[i]];}render();
    } else if(button.hasAttribute("data-stress-add")){items=[...items,...Array.from({length:Math.min(100,10000-items.length)},makeItem)];render();}
    else if(button.hasAttribute("data-stress-remove")){items=items.slice(0,Math.max(1,items.length-100));render();}
  }
  function onInput(event) {
    if(busy)return;
    if(event.target===filter){render();return;}
    if(event.target.matches("[data-note]")) {
      const item=items.find(item=>item.id===Number(event.target.closest("[data-row-id]").dataset.rowId));
      if(item)item.note=event.target.value;
    }
  }
  function onChange(event) {
    if(busy)return;
    if(event.target===mode){reconciler=null;render();}
    if(event.target===size) {
      const count=Number(size.value);items=Array.from({length:count},(_,i)=>items[i]||makeItem());filter.value="";render();
    }
  }
  let previousFrame, lastPublish=0;
  function watchFrame(now) {
    if(stopped)return;
    if(document.visibilityState==="visible") {
      if(metrics.visibleRows>0 && body.children.length===0)metrics.blankFrames++;
      if(previousFrame!==undefined) { const gap=now-previousFrame;metrics.worstFrameMs=Math.max(metrics.worstFrameMs,gap);if(gap>50)metrics.longFrames++; }
      previousFrame=now;
    } else previousFrame=undefined;
    if(now-lastPublish>500){publish();lastPublish=now;}
    frame=requestAnimationFrame(watchFrame);
  }
  let shiftObserver;
  if(globalThis.PerformanceObserver?.supportedEntryTypes?.includes("layout-shift")) {
    shiftObserver=new PerformanceObserver(list=>{for(const entry of list.getEntries())if(!entry.hadRecentInput){metrics.layoutShift+=entry.value;if(entry.sources?.some(source=>source.node && viewport.contains(source.node)))metrics.listLayoutShift+=entry.value;}});
    shiftObserver.observe({type:"layout-shift"});
  }
  async function runVerification() {
    busy=true;setRunning(false);const saved=items,savedFilter=filter.value;
    const controls=[...root.querySelectorAll("[data-stress-auto],[data-stress-update],[data-stress-shuffle],[data-stress-refresh],[data-stress-sort],[data-stress-add],[data-stress-remove],[data-stress-verify],select,[data-stress-filter]")];for(const control of controls)control.disabled=true;
    verify.textContent="Running checks…";report.textContent="Running real DOM checks…";
    const checks=[];
    const check=(name,passed)=>checks.push({name,passed:Boolean(passed)});
    try {
      items=items.map(item=>({...item}));filter.value="";mode.value="keyed";reconciler=null;render();
      const before=new Map([...body.children].map(row=>[row.dataset.rowId,row]));
      const first=items[0],row=before.get(String(first.id)),note=parts(row).note;
      first.note="Draft <safe> & café stays put";first.selected=true;render();
      note.focus({preventScroll:true});note.setSelectionRange(6,12);
      const keyedLosses=metrics.keyedFocusLosses;
      for(let i=0;i<24;i++){if(stopped)throw new DOMException("Widget disposed","AbortError");tick(true);await new Promise(resolve=>requestAnimationFrame(resolve));}
      check("24 generated-array updates preserve every row node",[...body.children].every(row=>before.get(row.dataset.rowId)===row));
      check("Updates preserve focus, draft and caret",document.activeElement===note && note.value===first.note && note.selectionStart===6 && note.selectionEnd===12 && metrics.keyedFocusLosses===keyedLosses);
      items=[...items].reverse();render();
      check("Reordering preserves focused node and selection",document.activeElement===note && note.selectionStart===6 && parts(row).select.getAttribute("aria-pressed")==="true" && row.isConnected);
      filter.value=first.label;render();
      check("Filtering keeps a matching focused row alive",row.isConnected && document.activeElement===note && parts(row).note.value===first.note);
      filter.value="";render();
      const existing=new Map([...body.children].map(row=>[row.dataset.rowId,row]));
      const extra=Array.from({length:100},makeItem);items=[...items,...extra];render();items=items.slice(0,-100);render();
      check("Add/remove preserves existing row nodes",[...body.children].every(row=>existing.get(row.dataset.rowId)===row));
      if($("[data-stress-refresh]")) {
        const stamp=metrics.serverStamp;
        await refreshFromServer();
        check("Fresh Axon render preserves focused input and local state",metrics.serverStamp>stamp && document.activeElement===note && row.isConnected && note.value===first.note && parts(row).select.getAttribute("aria-pressed")==="true");
      }
      note.focus({preventScroll:true});mode.value="replace";render();
      check("Negative control detects full redraw and focus loss",!row.isConnected && document.activeElement!==note && metrics.redrawFocusLosses>0 && metrics.lastCreated===items.length);
      mode.value="keyed";reconciler=null;items=saved;filter.value=savedFilter;render();
      check("Rendered ID cells match their stable row keys",[...body.children].every(row=>row.firstElementChild.textContent===row.dataset.rowId));
      check("No unexplained empty rendered frames",metrics.blankFrames===0);
      report.textContent=JSON.stringify({passed:checks.every(c=>c.passed),rows:items.length,checks,
        keyedFocusLosses:metrics.keyedFocusLosses,expectedFullRedrawFocusLosses:metrics.redrawFocusLosses,blankFrames:metrics.blankFrames,
        serverRefreshes:metrics.serverRefreshes,p95SteadyPatchMs:metrics.p95SteadyPatchMs,p95AllOperationsMs:metrics.p95PatchMs,worstFrameMs:metrics.worstFrameMs},null,2);
    } catch(error) { report.textContent=JSON.stringify({passed:false,error:String(error),checks},null,2); }
    finally {
      busy=false;
      if(!stopped){mode.value="keyed";items=saved;filter.value=savedFilter;reconciler=null;render();
        for(const control of controls)if(control.isConnected)control.disabled=false;
        verify.textContent="Run verification";}
    }
  }
  size.value=String(items.length);adopt();render();
  root.addEventListener("click",onClick);root.addEventListener("input",onInput);root.addEventListener("change",onChange);
  frame=requestAnimationFrame(watchFrame);
  return ()=>{stopped=true;serverRequest?.abort();clearTimeout(timer);cancelAnimationFrame(frame);shiftObserver?.disconnect();
    root.removeEventListener("click",onClick);root.removeEventListener("input",onInput);root.removeEventListener("change",onChange);};
}
