import {keyedList} from './keyed.js';

// Fixed-height windowing for lists and tables. Models own drafts/selection;
// focused rows stay mounted even outside the viewport. At most one extra row
// is pinned, so reorders preserve editing without materializing the dataset.
export function windowedList(parent,{viewport,key,create,update,readKey,rowHeight,overscan=6,createSpacer,onRender=()=>{}}) {
  if(!Number.isFinite(rowHeight)||rowHeight<=0||!Number.isInteger(overscan)||overscan<0||overscan>100)throw new RangeError('Invalid list window dimensions');
  let items=[],indexes=new Map(),disposed=false,frame;
  const doc=parent.ownerDocument;
  const spacer=height=>{
    const node=createSpacer?createSpacer():doc.createElement('div');
    node.dataset.webSpacer='';node.setAttribute('aria-hidden','true');node.style.height=`${height}px`;
    return node;
  };
  const existing=[...parent.children].filter(node=>!node.hasAttribute('data-web-spacer'));
  for(const node of existing)node.dataset.webWindowKey=String(readKey(node));
  const makePatch=()=>keyedList(parent,{
    key:entry=>entry.spacer?`gap:${entry.from}:${entry.to}`:`row:${entry.id}`,
    readKey:node=>node.hasAttribute('data-web-spacer')?node.dataset.webGap:`row:${readKey(node)}`,
    create:entry=>entry.spacer?spacer(entry.height):create(entry.item),
    update(node,entry){
      if(entry.spacer){node.dataset.webGap=`gap:${entry.from}:${entry.to}`;node.style.height=`${entry.height}px`;return;}
      node.dataset.webWindowKey=entry.id;node.style.height=`${rowHeight}px`;
      node.setAttribute('aria-rowindex',String(entry.index+2));update(node,entry.item);
    },
  });
  let patch=makePatch();
  function draw(){
    if(disposed)return;
    const start=Math.max(0,Math.min(items.length,Math.floor(viewport.scrollTop/rowHeight)-overscan));
    const end=Math.min(items.length,Math.ceil((viewport.scrollTop+viewport.clientHeight)/rowHeight)+overscan);
    const active=doc.activeElement?.closest?.('[data-web-window-key]');
    const pinned=active&&parent.contains(active)?indexes.get(active.dataset.webWindowKey):undefined;
    const positions=[];for(let i=start;i<end;i++)positions.push(i);
    if(pinned!==undefined&&(pinned<start||pinned>=end))positions.push(pinned);
    positions.sort((a,b)=>a-b);
    const entries=[];let cursor=0;
    for(const index of positions){
      if(index>cursor)entries.push({spacer:true,from:cursor,to:index,height:(index-cursor)*rowHeight});
      entries.push({id:String(key(items[index])),item:items[index],index});cursor=index+1;
    }
    if(cursor<items.length)entries.push({spacer:true,from:cursor,to:items.length,height:(items.length-cursor)*rowHeight});
    const result=patch(entries);
    onRender({...result,start,end,logical:items.length,mounted:positions.length,pinned:pinned!==undefined});
    return {...result,start,end,logical:items.length,mounted:positions.length};
  }
  function schedule(){if(!disposed&&frame===undefined)frame=requestAnimationFrame(()=>{frame=undefined;draw();});}
  viewport.addEventListener('scroll',schedule,{passive:true});
  parent.addEventListener('focusout',schedule);
  doc.defaultView?.addEventListener('resize',schedule);
  const Observer=doc.defaultView?.ResizeObserver,observer=Observer?new Observer(schedule):null;
  observer?.observe(viewport);
  return {
    update(next,{preserveAnchor=true,retain=true}={}){
      const active=doc.activeElement?.closest?.('[data-web-window-key]');
      const anchorIndex=Math.min(items.length-1,Math.max(0,Math.floor(viewport.scrollTop/rowHeight)));
      const anchor=active&&parent.contains(active)?active.dataset.webWindowKey:items[anchorIndex]===undefined?null:String(key(items[anchorIndex]));
      const oldIndex=anchor===null?undefined:indexes.get(anchor);
      const offset=oldIndex===undefined?0:viewport.scrollTop-oldIndex*rowHeight;
      const found=new Map();next.forEach((item,index)=>{const id=String(key(item));if(found.has(id))throw new Error(`Duplicate list key: ${id}`);found.set(id,index);});
      items=next;indexes=found;
      if(!retain){parent.replaceChildren();patch=makePatch();}
      if(preserveAnchor&&oldIndex!==undefined&&indexes.has(anchor))viewport.scrollTop=Math.max(0,indexes.get(anchor)*rowHeight+offset);
      const max=Math.max(0,items.length*rowHeight-viewport.clientHeight);if(viewport.scrollTop>max)viewport.scrollTop=max;
      return draw();
    },
    render:draw,
    scrollToIndex(index){
      if(!Number.isInteger(index)||index<0||index>=items.length)throw new RangeError('List index out of range');
      viewport.scrollTop=Math.min(index*rowHeight,Math.max(0,items.length*rowHeight-viewport.clientHeight));
      return draw();
    },
    dispose(){disposed=true;if(frame!==undefined)cancelAnimationFrame(frame);observer?.disconnect();doc.defaultView?.removeEventListener('resize',schedule);viewport.removeEventListener('scroll',schedule);parent.removeEventListener('focusout',schedule);},
  };
}
