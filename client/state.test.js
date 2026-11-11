import { test, expect } from 'bun:test';
import { signal, state, computed, clearState, createAction } from './state.js';

test('signals notify changes and computed subscriptions dispose',()=>{
  const a=signal(2),b=signal(3),sum=computed([a,b],(x,y)=>x+y);
  const seen=[];const stop=sum.subscribe(value=>seen.push(value),{immediate:true});
  a.set(value=>value+1);b.set(7);expect(seen).toEqual([5,6,10]);
  stop();sum.dispose();a.set(100);expect(sum.get()).toBe(10);
});
test('stable component state survives remount and can be explicitly cleared',()=>{
  clearState('fixture:');const first=state('fixture:count',0);first.set(42);
  expect(state('fixture:count',99)).toBe(first);expect(state('fixture:count',99).get()).toBe(42);
  clearState('fixture:');expect(state('fixture:count',99).get()).toBe(99);
});
test('typed JSON action sends CSRF and tracks pending result',async()=>{
  let release;
  const action=createAction('/api/tasks',{base:'https://app.test/',csrf:()=> 'token',fetch:async(url,options)=>{
    expect(String(url)).toBe('https://app.test/api/tasks');expect(options.headers['X-CSRF-Token']).toBe('token');
    expect(JSON.parse(options.body)).toEqual({title:'café'});
    await new Promise(resolve=>release=resolve);
    return Response.json({id:'42'},{status:201});
  }});
  const request=action.run({title:'café'});expect(action.pending.get()).toBe(true);
  await expect(action.run({title:'second'})).rejects.toThrow('already pending');release();
  expect(await request).toEqual({id:'42'});expect(action.pending.get()).toBe(false);
});
test('optimistic mutations roll back on validation errors',async()=>{
  let count=0;
  const action=createAction('/action',{base:'https://app.test',fetch:async()=>Response.json({error:'Invalid title'},{status:422})});
  await expect(action.run({},{optimistic:()=>{count++;return()=>count--;}})).rejects.toThrow('Invalid title');
  expect(count).toBe(0);expect(action.error.get().status).toBe(422);expect(action.pending.get()).toBe(false);
});
test('actions reject external origins and unsupported methods',async()=>{
  expect(()=>createAction('https://evil.test/action',{base:'https://app.test/'})).toThrow('same-origin');
  const action=createAction('/action',{base:'https://app.test/'});
  await expect(action.run({},{method:'GET'})).rejects.toThrow('Unsupported mutation');
});
