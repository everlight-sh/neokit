import {test,expect} from 'bun:test';
import {Window} from 'happy-dom';
import {windowedList} from './windowed.js';
import {readData} from './data.js';

function testWindow(){const window=new Window();for(const name of ['Error','TypeError','SyntaxError','RangeError','RegExp'])if(!window[name])window[name]=globalThis[name];return window;}
function fixture(){
  const window=testWindow(),document=window.document;
  globalThis.requestAnimationFrame=window.requestAnimationFrame.bind(window);
  globalThis.cancelAnimationFrame=window.cancelAnimationFrame.bind(window);
  const viewport=document.createElement('div'),body=document.createElement('div');
  document.body.append(viewport);viewport.append(body);
  Object.defineProperty(viewport,'clientHeight',{value:500,configurable:true});
  const list=windowedList(body,{viewport,rowHeight:50,overscan:3,key:item=>item.id,readKey:node=>node.dataset.id,
    create(){const node=document.createElement('div');node.append(document.createElement('input'));return node;},
    update(node,item){node.dataset.id=item.id;const input=node.firstChild;if(input.value!==item.note)input.value=item.note;}});
  return {window,document,body,viewport,list,models:Array.from({length:10000},(_,i)=>({id:String(i+1),note:`Draft ${i+1}`}))};
}
test('10,000 logical records use a bounded window, including the final record',()=>{
  const f=fixture();f.list.update(f.models);
  expect(f.body.querySelectorAll('input').length).toBe(13);
  f.list.scrollToIndex(9999);
  expect(f.body.querySelector('[data-id="10000"] input').value).toBe('Draft 10000');
  expect(f.body.querySelectorAll('input').length).toBeLessThanOrEqual(16);
  f.list.scrollToIndex(0);expect(f.body.querySelector('[data-id="1"] input').value).toBe('Draft 1');
  f.list.update([]);expect(f.body.children.length).toBe(0);
  f.list.dispose();f.window.happyDOM.abort();
});
test('updates, reordering and offscreen scrolling preserve the focused editor and caret',()=>{
  const f=fixture();f.list.update(f.models);const row=f.body.querySelector('[data-id="1"]'),input=row.firstChild;
  f.models[0].note='Edited draft';input.value='Edited draft';input.focus();input.setSelectionRange(2,5);
  f.list.update(f.models.map(item=>({...item})));
  f.list.scrollToIndex(9999);
  expect(f.body.querySelector('[data-id="1"]')).toBe(row);expect(f.document.activeElement).toBe(input);
  expect(input.selectionStart).toBe(2);expect(input.selectionEnd).toBe(5);
  f.list.update([...f.models].reverse());expect(f.document.activeElement).toBe(input);expect(input.value).toBe('Edited draft');
  expect(f.body.querySelectorAll('input').length).toBeLessThanOrEqual(17);
  const before=f.body.innerHTML;
  expect(()=>f.list.update([f.models[0],f.models[0]])).toThrow('Duplicate');expect(f.body.innerHTML).toBe(before);
  f.list.dispose();f.window.happyDOM.abort();
});
test('disposal cancels scroll work and drafts are recreated from models',async()=>{
  const f=fixture();f.list.update(f.models);f.models[0].note='Offscreen draft';
  f.list.scrollToIndex(9999);f.list.scrollToIndex(0);
  expect(f.body.querySelector('[data-id="1"] input').value).toBe('Offscreen draft');
  f.list.dispose();const before=f.body.innerHTML;f.viewport.scrollTop=10000;f.viewport.dispatchEvent(new f.window.Event('scroll'));
  await new Promise(resolve=>setTimeout(resolve,25));expect(f.body.innerHTML).toBe(before);f.window.happyDOM.abort();
});
test('viewport growth refreshes the mounted window',async()=>{
  const f=fixture();f.list.update(f.models);
  Object.defineProperty(f.viewport,'clientHeight',{value:1000,configurable:true});
  f.window.dispatchEvent(new f.window.Event('resize'));
  await new Promise(resolve=>setTimeout(resolve,30));
  expect(f.body.querySelectorAll('input').length).toBe(23);
  f.list.dispose();f.window.happyDOM.abort();
});
test('inert page data round trips escaped markup without creating executable nodes',()=>{
  const window=testWindow(),document=window.document;
  const value={note:'</template><script>alert(1)</script> & " café',count:10000};
  const escaped=JSON.stringify(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
  document.body.innerHTML=`<template data-web-data="rows">${escaped}</template>`;
  expect(readData(document,'rows')).toEqual(value);expect(document.querySelector('script')).toBeNull();
  expect(()=>readData(document,'rows',{maxLength:3})).toThrow('limit');
  expect(readData(document,'rows',{remove:true})).toEqual(value);expect(()=>readData(document,'rows')).toThrow('Missing');
  window.happyDOM.abort();
});
