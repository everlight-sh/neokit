export function mount(element, context) {
  const count=context.state('count',0), output=element.querySelector('output'), button=element.querySelector('button');
  const unsubscribe=count.subscribe(value=>output.textContent=String(value),{immediate:true});
  const click=()=>count.set(value=>value+1);button.addEventListener('click',click);
  return()=>{unsubscribe();button.removeEventListener('click',click);};
}
