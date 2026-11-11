// The native std:http_data writer emits inert escaped JSON, never script.
export function readData(root,name,{remove=false,maxLength=16*1024*1024}={}) {
  const template=[...root.querySelectorAll('template[data-web-data]')].find(node=>node.dataset.webData===name);
  if(!template)throw new Error(`Missing page data: ${name}`);
  const text=template.content.textContent;
  if(text.length>maxLength)throw new RangeError('Page data exceeds its limit');
  const value=JSON.parse(text);
  if(remove)template.remove();
  return value;
}
