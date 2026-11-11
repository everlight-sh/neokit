import {session,update} from './security.mjs';
export async function PATCH(request,{params}){
  const user=session(request.headers.get('cookie')||'');
  if(!user)return new Response('Unauthorized',{status:401});
  if(request.headers.get('origin')!==process.env.NEOKIT_ORIGIN||request.headers.get('x-csrf-token')!==user.csrf)return new Response('Forbidden',{status:403});
  let input;try{input=await request.json();}catch{return new Response('Malformed JSON',{status:400});}
  if(typeof input.title!=='string'||Buffer.byteLength(input.title)<1||Buffer.byteLength(input.title)>200)return Response.json({error:'Invalid title'},{status:422});
  const {id}=await params;
  if(!update(user.subject,id,input.title))return new Response('Not found',{status:404});
  return Response.json({ok:true,id},{headers:{'Cache-Control':'no-store'}});
}
