// Optional example/widget badge. Server time is the measured buffered handler;
// navigation TTFB also includes transport and browser scheduling.
export function mount(element) {
  const render=()=>{
    const entry=performance.getEntriesByType('navigation')[0];
    const metric=entry?.serverTiming?.find(metric=>metric.name==='render');
    const output=element.querySelector('output')||element;
    if(metric){output.textContent=`${metric.duration.toFixed(2)} ms server render`;element.title=`Measured on this request. Navigation TTFB: ${Math.max(0,entry.responseStart-entry.requestStart).toFixed(1)} ms. Excludes response delivery from server render time.`;}
    else output.textContent=document.body.dataset.renderMode?.startsWith('static')?'Static HTML · no server needed':'Server-rendered HTML';
  };
  render();window.addEventListener('load',render,{once:true});
  return()=>window.removeEventListener('load',render);
}
