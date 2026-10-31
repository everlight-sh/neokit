import { expect, test } from "bun:test";
import { keyedList } from "./keyed.js";

// Minimal independent DOM model. Native moves normally blur descendants;
// moveBefore preserves them. The real-browser page separately tests actual DOM.
function fixture(preservingMove = false) {
  const doc = { activeElement: null };
  const parent = {
    ownerDocument: doc, children: [],
    get firstElementChild() { return this.children[0] || null; },
    contains(node) { return this.children.some(row => row === node || row.input === node); },
    insertBefore(node, cursor) {
      if (node.parentNode) node.remove();
      this.children.splice(cursor === null ? this.children.length : this.children.indexOf(cursor), 0, node);
      node.parentNode = this;
    },
  };
  if (preservingMove) parent.moveBefore = (node, cursor) => {
    parent.children.splice(parent.children.indexOf(node),1);
    parent.children.splice(cursor===null?parent.children.length:parent.children.indexOf(cursor),0,node);
  };
  function create(item) {
    const node = {
      id: item.id, parentNode: null, localState: { clicks: 7 },
      get nextElementSibling() { const a=this.parentNode.children;return a[a.indexOf(this)+1] || null; },
      remove() {
        if(!this.parentNode)return;
        const p=this.parentNode;p.children.splice(p.children.indexOf(this),1);this.parentNode=null;
        if(doc.activeElement===this.input)doc.activeElement=null;
      },
    };
    node.input = {
      value: "typed draft", selectionStart: 2, selectionEnd: 5, selectionDirection: "forward", rangeWrites: 0,
      get isConnected() { return node.parentNode!==null; },
      focus() { doc.activeElement=this; },
      setSelectionRange(start,end,direction) { this.selectionStart=start;this.selectionEnd=end;this.selectionDirection=direction;this.rangeWrites++; },
    };
    return node;
  }
  const options = { key:item=>item.id, readKey:node=>node.id, create, update:(node,item)=>{node.display=item.value;} };
  return { doc,parent,create,options };
}

for (const nativeMove of [false,true]) {
  test(`keyed reorder preserves row, input, draft, caret and state (moveBefore=${nativeMove})`, () => {
    const f=fixture(nativeMove), initial=[1,2,3,4].map(id=>({id,value:0}));
    for(const item of initial)f.parent.insertBefore(f.create(item),null);
    const original=[...f.parent.children],input=original[1].input;input.focus();
    const patch=keyedList(f.parent,f.options);
    const first=patch(initial.map(item=>({...item,value:1})));
    expect(first.created).toBe(0);expect(first.removed).toBe(0);
    expect(input.rangeWrites).toBe(0);
    patch([4,2,1,3].map(id=>({id,value:9})));
    expect(f.parent.children.map(node=>node.id)).toEqual([4,2,1,3]);
    expect(f.parent.children[1]).toBe(original[1]);expect(f.doc.activeElement).toBe(input);
    expect(input.value).toBe("typed draft");expect([input.selectionStart,input.selectionEnd]).toEqual([2,5]);
    expect(original[1].localState.clicks).toBe(7);
  });
}

test("duplicate keys fail before changing the existing DOM", () => {
  const f=fixture(),patch=keyedList(f.parent,f.options);
  patch([{id:1},{id:2}]);const before=[...f.parent.children];
  expect(()=>patch([{id:2},{id:2}])).toThrow("Duplicate list keys");
  expect(f.parent.children).toEqual(before);
});

test("seeded structural updates match independent array order and preserve surviving identities", () => {
  const f=fixture(),patch=keyedList(f.parent,f.options);let seed=32817;
  const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed;};
  for(let step=0;step<500;step++) {
    const before=new Map(f.parent.children.map(node=>[node.id,node]));
    const ids=Array.from({length:40},(_,i)=>i+1).filter(()=>random()%3!==0);
    for(let i=ids.length-1;i>0;i--){const j=random()%(i+1);[ids[i],ids[j]]=[ids[j],ids[i]];}
    patch(ids.map(id=>({id,value:step})));
    expect(f.parent.children.map(node=>node.id)).toEqual(ids);
    for(const node of f.parent.children) {
      if(before.has(node.id))expect(node).toBe(before.get(node.id));
      expect(node.display).toBe(step);expect(node.localState.clicks).toBe(7);
    }
  }
});
