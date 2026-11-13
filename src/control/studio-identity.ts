/** Display identity only. Runtime hosts and signed sessions remain the routing authority. */
export const studioIdentityJs=String.raw`'use strict';
(async()=>{
 try{
  const response=await fetch('/__sbarbase/navigation',{credentials:'same-origin',cache:'no-store'});
  if(!response.ok)return;
  const {current}=await response.json();
  const name=value=>typeof value==='string'?Array.from(value.replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g,'').trim()).slice(0,120).join(''):'';
  const project=name(current?.project?.name),environment=name(current?.environment?.name);
  if(!project||!environment)return;
  const label=project+' / '+environment;
  const slug=Array.from((project+'-'+environment).normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,'-').replace(/^-+|-+$/g,'')).slice(0,100).join('')||'workspace';
  const replace=history.replaceState.bind(history);
  let written='';
  function update(){
   if(!location.pathname.startsWith('/project/'))return;
   const url=new URL(location.href);
   if(url.searchParams.get('workspace')!==slug){url.searchParams.set('workspace',slug);replace(history.state,'',url);}
   if(document.title!==written){const original=document.title;written=label+(original?' | '+original:'');document.title=written;}
  }
  for(const method of ['pushState','replaceState']){
   const original=history[method];
   history[method]=function(...args){const result=original.apply(this,args);update();return result;};
  }
  addEventListener('popstate',update);
  new MutationObserver(update).observe(document.head,{childList:true,subtree:true,characterData:true});
  update();
 }catch{}
})();`;

/** The upstream document has already been decompressed by fetch. Assets remain byte-for-byte. */
export function identifyStudioDocument(response:Response):Response {
 const headers=new Headers(response.headers);
 const policy=headers.get('content-security-policy')??'';
 const script=policy.match(/(?:^|;)\s*script-src-elem\s+([^;]+)/)?.[1]
  ??policy.match(/(?:^|;)\s*script-src\s+([^;]+)/)?.[1]
  ??policy.match(/(?:^|;)\s*default-src\s+([^;]+)/)?.[1];
 const nonce=script?.match(/'nonce-([A-Za-z0-9+/_=-]+)'/)?.[1];
 // Honor restrictive vendor policies without granting a new script source.
 const permitted=!script||!!nonce||script.split(/\s+/).includes("'self'");
 headers.delete('content-encoding');headers.delete('content-length');headers.delete('etag');
 headers.set('cache-control','no-store');
 const plain=new Response(response.body,{status:response.status,headers});
 if(!permitted)return plain;
 return new HTMLRewriter().on('head',{element(head){
  head.append('<script src="/__sbarbase/identity.js" defer'+(nonce?' nonce="'+nonce+'"':'')+'></script>',{html:true});
 }}).transform(plain);
}
