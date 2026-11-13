import {createHash} from 'node:crypto';

export const STUDIO_SMTP_ASSET_VERSION='3699278-smtp-v1';

/** Compatibility for original Studio 369927892fde63d07b199d4e80fe05ae780573d5 only.
 * Each path is bound to its entire original module, not a guessed minified fragment.
 * Public source modules and their original chunk hashes are retained in the test fixtures. */
const ASSETS=new Map<string,{kind:'query'|'form';sha256:string}>([
 ['/_next/static/chunks/08dh2y9gjjf0f.js',{kind:'query',sha256:'23ecd28d7f83a20a5bbbbec1b6997c85f9d6a126fb430b36f778b646a4bf2e82'}],
 ['/_next/static/chunks/13nn7t7hm5mkk.js',{kind:'query',sha256:'8c995b4df3defbb11ed5f65c05130409f7ee1308c83f923c8522ddeb6e273a41'}],
 ['/_next/static/chunks/25xhey2_i77pj.js',{kind:'query',sha256:'b8d4fa715fab98632e68f104583cf6b3cee8d822156595a79d063c6ed74f8119'}],
 ['/_next/static/chunks/3l8ryugu_z83g.js',{kind:'query',sha256:'c1525dc346bcb0ac7325986106c39a5089e5e597207d4e25c17dfa1cf2bb4ace'}],
 ['/_next/static/chunks/3vxfscsvszhgj.js',{kind:'query',sha256:'8f2048fb0970b586058b94e3e19f77d4d5e91fe20fa22d5dbe7fa33a86b829e4'}],
 ['/_next/static/chunks/0ypeum1urxotu.js',{kind:'form',sha256:'cc1606f3ce8ef0a58755a12d5ddd109dbb5c31b7e7806d65d12a28f6293ce28b'}],
]);
const MAX_ASSET_BYTES=256*1024;
const FAILURE='This Studio build is not supported by the SMTP compatibility adapter. Reload Studio after the operator checks its image.';

export function isStudioSmtpAsset(pathname:string):boolean {return ASSETS.has(pathname);}

function once(source:string,old:string,next:string):string {
 const index=source.indexOf(old);
 if(index<0||source.indexOf(old,index+old.length)>=0)throw new Error('Unexpected pinned Studio module');
 return source.slice(0,index)+next+source.slice(index+old.length);
}

function queryModule(source:string):string {
 const guard=source.match(/enabled:(\w+)&&(\w+)\.IS_PLATFORM&&void 0!==e&&"_"!==e/);
 const key=source.match(/queryKey:(\w+)\.authConfig\(e\)/);
 const prefetch=source.match(/e&&(\w+)\.prefetchQuery\(/);
 if(!guard||!key||!prefetch)throw new Error('Unexpected pinned Studio query');
 const hosted=guard[2]+'.IS_PLATFORM',base=key[1]+'.authConfig(e)';
 const scoped=`${hosted}?${base}:[...${base},__sbarbaseSmtpPage(e)?"sbarbase-smtp":"sbarbase-auth-unavailable"]`;
 if(source.split('queryKey:'+base).length!==3)throw new Error('Unexpected pinned Studio query keys');
 source=source.replaceAll('queryKey:'+base,'queryKey:'+scoped);
 source=once(source,guard[0],`enabled:${guard[1]}&&(${hosted}||__sbarbaseSmtpPage(e))&&void 0!==e&&"_"!==e`);
 source=once(source,prefetch[0],`e&&(${hosted}||__sbarbaseSmtpPage(e))&&${prefetch[1]}.prefetchQuery(`);
 return once(source,'"use strict";',
  '"use strict";function __sbarbaseSmtpPage(ref){return ref==="default"&&globalThis.location?.pathname==="/project/default/auth/smtp"}');
}

function formModule(source:string):string {
 source=once(source,'"use strict";','"use strict";var __sbarbaseHosted=e.i(10429).IS_PLATFORM;');
 // Keep the hook call and the callback's finally intact. Self-hosted disable retains templates.
 source=once(source,'try{(await Promise.allSettled(N.AUTH_TEMPLATE_RESET_TYPES.map(t=>D({projectRef:e,template:t}))))',
  'try{__sbarbaseHosted&&(await Promise.allSettled(N.AUTH_TEMPLATE_RESET_TYPES.map(t=>D({projectRef:e,template:t}))))');
 const cloud='(0,t.jsxs)("p",{children:["Switching back to the built-in SMTP service will"," ",(0,t.jsx)("strong",{className:"text-foreground",children:"reset any custom email templates"})," and"," ",(0,t.jsx)("strong",{className:"text-foreground",children:"reduce the email rate limit to 2 emails per hour"}),"."]})';
 source=once(source,cloud,`__sbarbaseHosted?${cloud}:(0,t.jsx)("p",{children:"Custom SMTP will be disabled. Authentication emails will not be sent. Existing email templates will be kept."})`);
 source=once(source,'!s&&(0,t.jsx)("p",{children:"You won\'t be able to edit email templates until you set up custom SMTP again or upgrade your plan."})',
  '__sbarbaseHosted&&!s&&(0,t.jsx)("p",{children:"You won\'t be able to edit email templates until you set up custom SMTP again or upgrade your plan."})');
 return once(source,'"Your SMTP credentials will always be encrypted in our database."',
  '(__sbarbaseHosted?"Your SMTP credentials will always be encrypted in our database.":"Leave the password field empty to keep the stored password.")');
}

function transform(source:string,asset:{kind:'query'|'form';sha256:string}):string {
 const prefix=asset.kind==='query'?'14703,294285,e=>{':'980668,e=>{',suffix=asset.kind==='query'?'],14703)}':'],980668)}';
 const start=source.indexOf(prefix),last=source.indexOf(suffix,start);
 if(start<0||last<0||source.indexOf(prefix,start+prefix.length)>=0)throw new Error('Missing or repeated pinned Studio module');
 const end=last+suffix.length,module=source.slice(start,end);
 if(createHash('sha256').update(module).digest('hex')!==asset.sha256)throw new Error('Unrecognized pinned Studio module');
 return source.slice(0,start)+(asset.kind==='query'?queryModule(module):formModule(module))+source.slice(end);
}

function representationHeaders(response:Response):Headers {
 const headers=new Headers(response.headers);
 // Transport must decode these assets before transformation, including non-success responses.
 for(const name of ['content-encoding','content-length','etag','last-modified','content-md5','digest','content-range','accept-ranges'])headers.delete(name);
 headers.set('cache-control','no-store');
 return headers;
}

async function boundedText(response:Response):Promise<string> {
 if(!response.body)return '';
 const reader=response.body.getReader(),parts:Uint8Array[]=[];let size=0;
 try {
  while(true){const next=await reader.read();if(next.done)break;
   size+=next.value.byteLength;if(size>MAX_ASSET_BYTES){void reader.cancel().catch(()=>{});throw new Error('Studio asset is too large');}
   parts.push(next.value);
  }
 }finally{reader.releaseLock();}
 const bytes=new Uint8Array(size);let offset=0;for(const part of parts){bytes.set(part,offset);offset+=part.byteLength;}
 return new TextDecoder('utf-8',{fatal:true}).decode(bytes);
}

/** Takes a decoded GET representation (proxy fetch uses decompress:true). Known assets fail closed
 * on a changed module; unrelated paths are left untouched. Fetch a GET body before handling HEAD. */
export async function transformStudioSmtpAsset(pathname:string,response:Response):Promise<Response> {
 const asset=ASSETS.get(pathname);if(!asset)return response;
 const headers=representationHeaders(response);
 if(response.status>=400)return new Response(response.body,{status:response.status,statusText:response.statusText,headers});
 try {
  if(response.status!==200||!headers.get('content-type')?.includes('javascript'))throw new Error('Unexpected Studio representation');
  const text=transform(await boundedText(response),asset);
  headers.set('x-sbarbase-studio-smtp','3699278');
  return new Response(text,{status:200,headers});
 }catch {
  if(response.body&&!response.bodyUsed)void response.body.cancel().catch(()=>{});
  return new Response(FAILURE,{status:503,headers:{'content-type':'text/plain; charset=utf-8','cache-control':'no-store'}});
 }
}
