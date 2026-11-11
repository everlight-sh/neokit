import { test, expect } from 'bun:test';
import { Window } from 'happy-dom';
import { startStreaming } from './stream.js';
const tick = () => new Promise(resolve => setTimeout(resolve, 20));
function fixture() {
  const window = new Window({url:'http://localhost/'});
  for(const key of ['Error','SyntaxError','TypeError','RangeError'])if(!window[key])window[key]=globalThis[key];
  const document = window.document;
  Object.defineProperty(document, 'readyState', {value:'loading',configurable:true});
  document.body.innerHTML = '<main data-web-stream="request_1"><section data-web-boundary="slow" data-web-state="pending" aria-busy="true">Loading slow</section><section data-web-boundary="fast" data-web-state="pending" aria-busy="true">Loading fast</section></main>';
  return {window,document};
}
// happy-dom 20.9 retains its delivery closure only through WeakRef. A real
// MutationObserver owns that closure; keep it alive when creating this fixture.
function observe(options) {
  const NativeWeakRef=globalThis.WeakRef;
  globalThis.WeakRef=class extends NativeWeakRef { constructor(value){super(value);this.keepAlive=value;} };
  try { return startStreaming(options); } finally { globalThis.WeakRef=NativeWeakRef; }
}
function append(document, html) { const element = document.createElement('div'); element.innerHTML = html; document.body.append(element); }
test('waits for completion marker, resolves out of order and ignores stale requests', async () => {
  const {document} = fixture(); let mounted=0,unmounted=0;
  const stop=observe({document,beforeReplace(){unmounted++;},afterReplace(){mounted++;}});
  append(document,'<template data-web-fragment="request_1:fast" data-web-state="ready"><b>Fast result</b></template>');await tick();
  expect(document.querySelector('[data-web-boundary="fast"]').textContent).toBe('Loading fast');
  append(document,'<template data-web-ready="request_1:fast"></template>');await tick();
  expect(document.querySelector('[data-web-boundary="fast"]').textContent).toBe('Fast result');
  expect(document.querySelector('[data-web-boundary="slow"]').textContent).toBe('Loading slow');
  append(document,'<template data-web-fragment="old:slow">Stale</template><template data-web-ready="old:slow"></template>');await tick();
  expect(document.querySelector('[data-web-boundary="slow"]').textContent).toBe('Loading slow');
  append(document,'<template data-web-fragment="request_1:slow" data-web-state="ready">Slow result</template><template data-web-ready="request_1:slow"></template><template data-web-stream-end="request_1"></template>');await tick();
  expect(document.querySelector('[data-web-boundary="slow"]').textContent).toBe('Slow result');expect(mounted).toBe(2);expect(unmounted).toBe(2);stop();
});
test('rejects executable fragments and completes pending fallbacks as errors', async () => {
  const {document} = fixture();const stop=observe({document});
  append(document,'<template data-web-fragment="request_1:fast" data-web-state="ready"><img src="x" onerror="alert(1)"></template><template data-web-ready="request_1:fast"></template><template data-web-stream-end="request_1"></template>');await tick();
  expect(document.querySelector('[data-web-boundary="fast"]').dataset.webState).toBe('error');expect(document.querySelector('[data-web-boundary="fast"] img')).toBeNull();
  expect(document.querySelector('[data-web-boundary="slow"]').dataset.webState).toBe('error');stop();
});
test('late module arrival processes completed fragments and ends truncated fallbacks', async () => {
  const {document} = fixture();
  Object.defineProperty(document, 'readyState', {value:'complete'});
  append(document,'<template data-web-fragment="request_1:fast" data-web-state="ready">Completed</template><template data-web-ready="request_1:fast"></template>');
  const stop=observe({document});
  expect(document.querySelector('[data-web-boundary="fast"]').textContent).toBe('Completed');
  expect(document.querySelector('[data-web-boundary="slow"]').dataset.webState).toBe('error');stop();
});
