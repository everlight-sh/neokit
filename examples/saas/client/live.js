import {subscribe} from '../../../client/realtime.js';
export function validDashboard(value){
  return value!==null&&typeof value==='object'&&['tick','stamp','amount','revenue','signups'].every(key=>Number.isSafeInteger(value[key])&&value[key]>=0)&&value.amount<=10000&&typeof value.kind==='string'&&value.kind.length<=48&&typeof value.company==='string'&&value.company.length<=120;
}
export function mount(root){
  const find=name=>root.querySelector(`[data-live-${name}]`),money=value=>`$${value.toLocaleString('en-US')}`;
  const points=[];let count=0,paused=false;
  const subscription=subscribe('/api/events',{
    events:{dashboard:{validate:validDashboard,receive(value,{id}){
      count++;find('revenue').textContent=money(value.revenue);find('signups').textContent=value.signups.toLocaleString('en-US');find('count').textContent=String(count);
      const at=new Date(value.stamp).toLocaleTimeString('en-US',{hour12:false});find('updated').textContent=`Last event ${at}`;find('cursor').textContent=`Event ${id}`;
      points.push(value.amount);if(points.length>60)points.shift();const max=Math.max(100,...points);
      find('line').setAttribute('d',points.map((amount,index)=>`${index?'L':'M'}${(index*600/Math.max(1,points.length-1)).toFixed(1)} ${(130-amount/max*120).toFixed(1)}`).join(' '));
      find('chart-label').textContent=`Latest payment ${money(value.amount)} · ${points.length} samples · server event ${at}`;
      const feed=find('feed');feed.querySelector('.live-empty')?.remove();const row=document.createElement('li');
      for(const [tag,text] of [['strong',value.kind],['span',`${value.company}${value.amount?` · ${money(value.amount)}`:''}`],['time',at]]){const node=document.createElement(tag);node.textContent=text;row.append(node);}
      feed.prepend(row);while(feed.children.length>12)feed.lastElementChild.remove();
    }}},
    onStatus(state){const status=find('status');status.textContent={live:'Live',connecting:'Connecting',reconnecting:'Reconnecting',paused:'Paused',disconnected:'Disconnected',closed:'Closed'}[state]||state;status.dataset.state=state;},
    onReset(){points.length=0;find('feed').replaceChildren();find('updated').textContent='Resynchronizing after a replay gap';},
    onError(){find('updated').textContent='An invalid event was ignored';}
  });
  const toggle=()=>{paused=!paused;find('toggle').textContent=paused?'Resume':'Pause';if(paused)subscription.pause();else subscription.resume();};find('toggle').addEventListener('click',toggle);
  return()=>{find('toggle').removeEventListener('click',toggle);subscription.close();};
}
