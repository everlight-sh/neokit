import { test, expect } from 'bun:test';
import { Window } from 'happy-dom';
import { replaceContent, syncMetadata } from './dom.js';
function testWindow(){const window=new Window();for(const name of ['Error','TypeError','SyntaxError','RangeError','RegExp'])if(!window[name])window[name]=globalThis[name];return window;}

test('nested layout and explicit persistent nodes retain identity and edited values',()=>{
  const window=testWindow();const document=window.document;
  const current=document.createElement('main');document.body.append(current);
  current.innerHTML='<div data-web-layout="outer"><aside data-web-persist="sidebar"><input value="original"></aside><div data-web-outlet="outer"><div data-web-layout="inner"><nav>Inner nav</nav><div data-web-outlet="inner"><p>Old page</p></div></div></div></div>';
  const outer=current.firstElementChild,inner=current.querySelector('[data-web-layout="inner"]'),input=current.querySelector('input');input.value='draft';
  const next=document.createElement('main');next.innerHTML='<div data-web-layout="outer"><aside data-web-persist="sidebar"><input value="reset"></aside><div data-web-outlet="outer"><div data-web-layout="inner"><nav>New nav</nav><div data-web-outlet="inner"><p>New page</p></div></div></div></div>';
  replaceContent(current,next);
  expect(current.firstElementChild).toBe(outer);expect(current.querySelector('[data-web-layout="inner"]')).toBe(inner);
  expect(current.querySelector('input')).toBe(input);expect(input.value).toBe('draft');
  expect(current.textContent).toContain('New page');expect(current.textContent).toContain('Inner nav');expect(current.textContent).not.toContain('Old page');
});
test('navigation replaces metadata without duplicating canonical or description tags',()=>{
  const window=testWindow();globalThis.CSS=window.CSS;
  const document=window.document;document.head.innerHTML='<meta name="description" content="old"><link rel="canonical" href="https://app.test/old">';
  const page=new window.DOMParser().parseFromString('<html><head><meta name="description" content="new"><meta property="og:title" content="New"><link rel="canonical" href="https://app.test/new"></head><body></body></html>','text/html');
  syncMetadata(document,page);syncMetadata(document,page);
  expect(document.querySelectorAll('meta[name="description"]').length).toBe(1);
  expect(document.querySelector('meta[name="description"]').content).toBe('new');
  expect(document.querySelector('link[rel="canonical"]').href).toBe('https://app.test/new');
  const plain=new window.DOMParser().parseFromString('<html><head><title>Plain</title></head><body></body></html>','text/html');
  syncMetadata(document,plain);
  expect(document.querySelector('meta[name="description"]')).toBeNull();
  expect(document.querySelector('link[rel="canonical"]')).toBeNull();
  expect(document.querySelector('meta[property="og:title"]')).toBeNull();
  window.happyDOM.abort();
});
