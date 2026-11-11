// Matched benchmark fixture: native session format and durable document updates.
import {createHmac,timingSafeEqual} from 'node:crypto';
import {readFileSync,openSync,writeFileSync,fsyncSync,closeSync,renameSync} from 'node:fs';
import {join} from 'node:path';
export function session(cookie) {
  const matches=cookie.split(';').map(x=>x.trim()).filter(x=>x.startsWith('neokit_session='));
  if(matches.length!==1)return null;
  const value=matches[0].slice(15),parts=value.split('.');
  if(parts.length!==5||parts[0]!=='v1'||Number(parts[3])<=Date.now())return null;
  const body=parts.slice(0,4).join('.'),actual=Buffer.from(parts[4],'base64url');
  const expected=createHmac('sha256',process.env.NEOKIT_SESSION_SECRET).update(body).digest();
  if(actual.length!==expected.length||!timingSafeEqual(actual,expected))return null;
  return {subject:Buffer.from(parts[1],'base64url').toString(),csrf:parts[2]};
}
export function database(){return JSON.parse(readFileSync(join(process.env.NEOKIT_DATA_DIR,'tasks.json'),'utf8'));}
export function update(owner,id,title){
  const value=database(),task=value.tasks.find(x=>x.id===id&&x.owner===owner);
  if(!task)return false;
  task.title=title;task.done=false;
  const directory=process.env.NEOKIT_DATA_DIR,destination=join(directory,'tasks.json'),temporary=destination+'.next.tmp';
  const fd=openSync(temporary,'w',0o600);
  try{writeFileSync(fd,JSON.stringify(value));fsyncSync(fd);}finally{closeSync(fd);}
  renameSync(temporary,destination);
  const dirfd=openSync(directory,'r');try{fsyncSync(dirfd);}finally{closeSync(dirfd);}
  return true;
}
