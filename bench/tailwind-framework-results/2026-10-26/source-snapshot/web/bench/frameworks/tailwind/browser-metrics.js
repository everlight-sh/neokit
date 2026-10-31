// Benchmark-only instrumentation, loaded exclusively by ?metrics=1.
// Uses the browser's real navigation/resource/paint timing APIs. No fetches.
(() => {
  const lcp = [], tasks = [];
  for (const [type, entries] of [["largest-contentful-paint", lcp], ["longtask", tasks]]) {
    try { new PerformanceObserver(list => entries.push(...list.getEntries())).observe({type, buffered:true}); } catch {}
  }
  let result;
  function render() {
    let output = document.querySelector('#browser-metrics');
    if (!output) {
      output = document.createElement('output'); output.id = 'browser-metrics';
      output.setAttribute('data-testid','browser-metrics'); output.setAttribute('aria-label','Browser measurements');
      output.style.cssText='display:block;font:9px monospace;white-space:pre-wrap;overflow-wrap:anywhere;margin:20px';
      document.body.append(output);
    }
    output.textContent = JSON.stringify(result);
  }
  function sample() {
    if (result) return;
    const nav = performance.getEntriesByType('navigation')[0];
    const resources = performance.getEntriesByType('resource').filter(r => !r.name.endsWith('/browser-metrics.js'));
    const assets = resources.map(r => ({url:new URL(r.name).pathname,initiator:r.initiatorType,
      encoded:r.encodedBodySize,decoded:r.decodedBodySize,transfer:r.transferSize,duration_ms:r.duration}));
    result = {ready:true,visibility:document.visibilityState,user_agent:navigator.userAgent,
      viewport:[innerWidth,innerHeight],device_pixel_ratio:devicePixelRatio,
      ttfb_ms:nav.responseStart,dom_content_loaded_ms:nav.domContentLoadedEventEnd,
      load_ms:nav.loadEventEnd,fcp_ms:performance.getEntriesByName('first-contentful-paint')[0]?.startTime ?? null,
      lcp_ms:lcp.at(-1)?.startTime ?? null,long_tasks_ms:tasks.reduce((s,t)=>s+t.duration,0),
      html_encoded_bytes:nav.encodedBodySize,html_decoded_bytes:nav.decodedBodySize,
      resource_encoded_bytes:assets.reduce((s,r)=>s+r.encoded,0),resource_decoded_bytes:assets.reduce((s,r)=>s+r.decoded,0),
      js_encoded_bytes:assets.filter(r=>r.url.endsWith('.js')).reduce((s,r)=>s+r.encoded,0),
      js_decoded_bytes:assets.filter(r=>r.url.endsWith('.js')).reduce((s,r)=>s+r.decoded,0),
      css_encoded_bytes:assets.filter(r=>r.url.endsWith('.css')).reduce((s,r)=>s+r.encoded,0),
      css_decoded_bytes:assets.filter(r=>r.url.endsWith('.css')).reduce((s,r)=>s+r.decoded,0),assets};
    render();
  }
  addEventListener('load',()=>setTimeout(sample,1500),{once:true});
  if(document.readyState==='complete')setTimeout(sample,1500);
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-increment]'); if(!button || !result)return;
    const start=performance.now();requestAnimationFrame(()=>requestAnimationFrame(()=>{
      result.click_to_second_frame_ms=performance.now()-start;
      result.counter_value=document.querySelector('[data-count]')?.textContent;
      render();
    }));
  },true);
})();
