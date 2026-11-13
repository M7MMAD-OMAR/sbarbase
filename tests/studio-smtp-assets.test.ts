import {describe,expect,test} from 'bun:test';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {gzipSync} from 'node:zlib';
import manifest from './fixtures/studio-smtp/manifest.json';
import {isStudioSmtpAsset,transformStudioSmtpAsset,versionStudioSmtpAsset,studioSmtpCacheJs,STUDIO_SMTP_ASSET_VERSION} from '../src/control/studio-smtp-assets';

const fixtures=new Map(manifest.map(record=>[record.path,readFileSync(new URL('./fixtures/studio-smtp/'+record.fixture,import.meta.url),'utf8')]));
const queryRecords=manifest.filter(record=>record.kind==='query');
const formRecord=manifest.find(record=>record.kind==='form')!;
const smtpPath='/project/default/auth/smtp';
async function transformed(path:string,source=fixtures.get(path)!,headers:Record<string,string>={}) {
 const response=await transformStudioSmtpAsset(path,new Response(source,{headers:{'content-type':'application/javascript',...headers}}));
 return {response,text:await response.text()};
}
function queryModule(source:string,hosted=false,pathname=smtpPath) {
 const exports:Record<string,any>={},prefetches:any[]=[];
 const client={prefetchQuery:(options:any)=>{prefetches.push(options);return Promise.resolve();}};
 const imports:Record<number,unknown>={125356:{useQuery:(options:any)=>options},964574:{useQueryClient:()=>client},416340:{useCallback:(callback:unknown)=>callback},
  234745:{get:async()=>({data:{SMTP_HOST:'smtp.example.test'}}),handleError:()=>{throw Error('unexpected API error');}},10429:{IS_PLATFORM:hosted}};
 const entries=runInNewContext('['+source+']',{location:{pathname}});
 entries[2]({i:(id:number)=>{if(!(id in imports))throw Error('Unexpected import '+id);return imports[id];},
  s:(values:any[])=>{for(let i=0;i<values.length;i+=3)exports[values[i]]=values[i+2];}});
 return {exports,prefetches};
}
function renderDisableDialog(source:string,hosted:boolean) {
 const start=source.indexOf('let j=')+6,end=source.indexOf(';var E=',start);
 const expression=source.slice(start,end);
 const jsx=(type:string,props:unknown)=>({type,props});
 const dialog=runInNewContext('('+expression+')',{t:{jsx,jsxs:jsx},A:new Proxy({},{get:(_target,key)=>String(key)}),__sbarbaseHosted:hosted});
 const flatten=(node:any):string=>typeof node==='string'?node:Array.isArray(node)?node.map(flatten).join(' '):node?.props?flatten(node.props.children):'';
 return flatten(dialog({open:true,onOpenChange:()=>{},onConfirm:()=>{},blockEditingOnReset:false}));
}
async function runDisableSuccess(source:string,hosted:boolean) {
 const match=source.match(/onSuccess:async\(\)=>\{(.+?)\},onError:r/);
 if(!match)throw Error('Pinned disable callback absent');
 const resets:string[]=[],notices:string[]=[];let cleared=false,resolved=false;
 const callback=runInNewContext('(async()=>{'+match[1]+'})',{$:()=>{cleared=true;},__sbarbaseHosted:hosted,
  N:{AUTH_TEMPLATE_RESET_TYPES:['confirmation','invite']},D:({template}:{template:string})=>{resets.push(template);return Promise.resolve();},e:'default',d:{toast:{error:(notice:string)=>notices.push(notice)}},t:()=>{resolved=true;}});
 await callback();return {resets,notices,cleared,resolved};
}

describe('pinned native SMTP query compatibility',()=>{
 for(const record of queryRecords){
  test(record.fixture+' enables only its self-hosted SMTP screen and separates caches',async()=>{
   const {response,text}=await transformed(record.path);expect(response.status).toBe(200);
   const smtp=queryModule(text),options=smtp.exports.useAuthConfigQuery({projectRef:'default'});
   expect(options.enabled).toBe(true);
   expect(options.queryKey).toEqual(['projects','default','auth-config','sbarbase-smtp']);
   expect(smtp.exports.useAuthConfigQuery({projectRef:'default'},{enabled:false}).enabled).toBe(false);
   for(const ref of [undefined,'_','other-project'])expect(smtp.exports.useAuthConfigQuery({projectRef:ref}).enabled).toBe(false);
   const other=queryModule(text,false,'/project/default/auth/providers'),disabled=other.exports.useAuthConfigQuery({projectRef:'default'});
   expect(disabled.enabled).toBe(false);expect(disabled.queryKey).toEqual(['projects','default','auth-config','sbarbase-auth-unavailable']);
   const hosted=queryModule(text,true,'/project/cloud/auth/providers'),hostedOptions=hosted.exports.useAuthConfigQuery({projectRef:'cloud'});
   expect(hostedOptions.enabled).toBe(true);expect(hostedOptions.queryKey).toEqual(['projects','cloud','auth-config']);
   // The unmodified native mutation invalidates this base prefix, including the SMTP suffix.
   expect(options.queryKey.slice(0,3)).toEqual(smtp.exports.authKeys.authConfig('default'));
  });
  test(record.fixture+' isolates prefetch and preserves hosted prefetch',async()=>{
   const {text}=await transformed(record.path),smtp=queryModule(text);
   smtp.exports.useAuthConfigPrefetch({projectRef:'default'})();expect(smtp.prefetches).toHaveLength(1);
   expect(smtp.prefetches[0].queryKey).toEqual(['projects','default','auth-config','sbarbase-smtp']);
   const other=queryModule(text,false,'/project/default/auth/providers');other.exports.useAuthConfigPrefetch({projectRef:'default'})();expect(other.prefetches).toHaveLength(0);
   const wrong=queryModule(text);wrong.exports.useAuthConfigPrefetch({projectRef:'other'})();expect(wrong.prefetches).toHaveLength(0);
   const hosted=queryModule(text,true,'/project/cloud/auth/providers');hosted.exports.useAuthConfigPrefetch({projectRef:'cloud'})();
   expect(hosted.prefetches[0].queryKey).toEqual(['projects','cloud','auth-config']);
  });
 }
 test('original fixture independently reproduces the disabled native readback',()=>{
  const original=queryModule(fixtures.get(queryRecords[0]!.path)!);expect(original.exports.useAuthConfigQuery({projectRef:'default'}).enabled).toBe(false);
 });
});

describe('native self-hosted disable preserves mail templates',()=>{
 test('confirmation describes actual self-hosted behavior while hosted wording is retained',async()=>{
  const {text}=await transformed(formRecord.path),selfhost=renderDisableDialog(text,false),hosted=renderDisableDialog(text,true);
  expect(selfhost).toContain('Existing email templates will be kept.');expect(selfhost).toContain('Authentication emails will not be sent.');
  expect(selfhost).not.toContain('built-in SMTP');expect(selfhost).not.toContain('2 emails per hour');expect(selfhost).not.toContain('upgrade your plan');
  expect(hosted).toContain('reset any custom email templates');expect(hosted).toContain('2 emails per hour');expect(hosted).toContain('upgrade your plan');
  expect(text).toContain('Leave the password field empty to keep the stored password.');
 });
 test('self-hosted callback keeps templates, resolves and preserves pending cleanup',async()=>{
  const {text}=await transformed(formRecord.path);expect(await runDisableSuccess(text,false)).toEqual({resets:[],notices:[],cleared:true,resolved:true});
  expect(await runDisableSuccess(text,true)).toEqual({resets:['confirmation','invite'],notices:[],cleared:true,resolved:true});
  // The hook itself remains present and in the original order.
  const hook=/\(0,R\.useAuthTemplateResetMutation\)\(\)/g;
  expect(text.match(hook)).toEqual(fixtures.get(formRecord.path)!.match(hook));
 });
});

describe('asset binding and HTTP representation',()=>{
 test('exact path matching does not intercept unrelated or suffixed assets',()=>{
  for(const record of manifest)expect(isStudioSmtpAsset(record.path)).toBe(true);
  for(const path of ['/project/default/auth/smtp','/_next/static/chunks/unknown.js',queryRecords[0]!.path+'.map','/prefix'+queryRecords[0]!.path])expect(isStudioSmtpAsset(path)).toBe(false);
 });
 test('unrelated responses are not consumed',async()=>{
  const response=new Response('original');expect(await transformStudioSmtpAsset('/unrelated.js',response)).toBe(response);expect(response.bodyUsed).toBe(false);
 });
 for(const record of manifest)test(record.fixture+' refuses unknown, missing or duplicate module bytes',async()=>{
  const original=fixtures.get(record.path)!;
  for(const source of [original.replace('"use strict"','"strict"'),'',original+original]){
   const {response,text}=await transformed(record.path,source);expect(response.status).toBe(503);expect(text).toContain('SMTP compatibility');
  }
 });
 test('transformation preserves all bytes outside the known module',async()=>{
  const record=queryRecords[0]!,before='/* unrelated prefix */',after='/* unrelated suffix */';
  const {text}=await transformed(record.path,before+fixtures.get(record.path)+after);expect(text.startsWith(before)).toBe(true);expect(text.endsWith(after)).toBe(true);
 });
 test('decoded compressed responses lose stale validators and encoding',async()=>{
  const {response}=await transformed(queryRecords[0]!.path,undefined,{'content-encoding':'gzip','content-length':'999','etag':'"old"','last-modified':'yesterday','content-range':'bytes 0-999/1000','cache-control':'public,max-age=31536000,immutable'});
  expect(response.status).toBe(200);for(const name of ['content-encoding','content-length','etag','last-modified','content-range'])expect(response.headers.has(name)).toBe(false);
  expect(response.headers.get('cache-control')).toBe('no-store');expect(response.headers.get('content-type')).toContain('javascript');
 });
 test('encoded bytes and oversized assets fail closed',async()=>{
  for(const body of [gzipSync(fixtures.get(queryRecords[0]!.path)!),new Uint8Array(256*1024+1)]){
   const response=await transformStudioSmtpAsset(queryRecords[0]!.path,new Response(body,{headers:{'content-type':'application/javascript','content-encoding':'gzip'}}));
   expect(response.status).toBe(503);expect(response.headers.has('content-encoding')).toBe(false);
  }
 });
 test('non-JavaScript and partial success cannot pass executable original bytes',async()=>{
  for(const response of [new Response('bad',{headers:{'content-type':'text/html'}}),new Response(fixtures.get(queryRecords[0]!.path),{status:206,headers:{'content-type':'application/javascript'}})])
   expect((await transformStudioSmtpAsset(queryRecords[0]!.path,response)).status).toBe(503);
 });
 test('redirects and not-modified responses cannot reuse an unverified cached script',async()=>{
  for(const response of [new Response(null,{status:304,headers:{etag:'old'}}),new Response(null,{status:307,headers:{location:'/unverified.js'}})])
   expect((await transformStudioSmtpAsset(queryRecords[0]!.path,response)).status).toBe(503);
 });
 test('decoded upstream errors keep their status without stale representation headers',async()=>{
  const response=await transformStudioSmtpAsset(queryRecords[0]!.path,new Response('unavailable',{status:502,headers:{'content-encoding':'gzip','etag':'old'}}));
  expect(response.status).toBe(502);expect(await response.text()).toBe('unavailable');expect(response.headers.has('content-encoding')).toBe(false);expect(response.headers.has('etag')).toBe(false);
 });
});

function cacheDom(){
 const origin='https://studio.example.test',network:{kind:string;url:string}[]=[],calls:{kind:string;value:unknown}[]=[];
 class Element {
  baseURI=origin+'/project/default/auth/smtp';attributes:Record<string,string>={};
  getAttribute(name:string){if(arguments.length<1||typeof name==='symbol')throw new TypeError('DOMString required');return this.attributes[String(name).toLowerCase()]??null;}
  setAttribute(name:string,value:unknown){
   if(typeof name==='symbol'||typeof value==='symbol')throw new TypeError('DOMString required');
   const key=String(name).toLowerCase(),text=String(value);calls.push({kind:'attribute',value});this.attributes[key]=text;
   if((this instanceof HTMLScriptElement&&key==='src')||(this instanceof HTMLLinkElement&&key==='href'))network.push({kind:'attribute',url:text});
  }
 }
 class HTMLScriptElement extends Element {
  get src():string{return this.attributes.src??'';}
  set src(value:unknown){if(typeof value==='symbol')throw new TypeError('DOMString required');calls.push({kind:'src',value});this.attributes.src=String(value);network.push({kind:'script',url:this.attributes.src});}
 }
 class HTMLLinkElement extends Element {
  rel='';as='';
  get href():string{return this.attributes.href??'';}
  set href(value:unknown){if(typeof value==='symbol')throw new TypeError('DOMString required');calls.push({kind:'href',value});this.attributes.href=String(value);network.push({kind:'link',url:this.attributes.href});}
 }
 const context={URL,Symbol,location:{origin,href:origin+'/project/default/auth/smtp'},Element,HTMLScriptElement,HTMLLinkElement};
 const evaluate=()=>runInNewContext(studioSmtpCacheJs,context);
 return{origin,network,calls,Element,HTMLScriptElement,HTMLLinkElement,evaluate};
}

describe('scoped native Studio CDN cache versioning',()=>{
 test('known same-origin chunks get one current version, preserving other values and hash',()=>{
  const origin='https://studio.example.test';
  for(const record of manifest){
   const updated=new URL(versionStudioSmtpAsset(record.path+'?existing=a%20b&sbarbase_smtp=old&sbarbase_smtp=duplicate#part',origin));
   expect(updated.origin).toBe(origin);expect(updated.pathname).toBe(record.path);expect(updated.searchParams.get('existing')).toBe('a b');expect(updated.hash).toBe('#part');
   expect(updated.searchParams.getAll('sbarbase_smtp')).toEqual([STUDIO_SMTP_ASSET_VERSION]);
   expect(versionStudioSmtpAsset(updated.href,origin)).toBe(updated.href);
  }
 });
 test('API, unrelated, other-origin, credentialed and non-HTTP URLs stay unchanged',()=>{
  const origin='https://studio.example.test',path=manifest[0]!.path;
  for(const value of ['/api/platform/auth/default/config','/_next/static/chunks/unrelated.js','/style.css','https://another.example.test'+path,
   'http://studio.example.test'+path,'https://user:password@studio.example.test'+path,'javascript:alert(1)','data:text/javascript,alert(1)','blob:https://studio.example.test/test'])
   expect(versionStudioSmtpAsset(value,origin)).toBe(value);
  expect(versionStudioSmtpAsset(path,'https://user:password@studio.example.test')).toBe(path);
  expect(versionStudioSmtpAsset(path,'file:///tmp/')).toBe(path);
  expect(versionStudioSmtpAsset(path,'not a URL')).toBe(path);
 });
 test('bootstrap versions script.src before the native setter starts its request',()=>{
  const dom=cacheDom(),native=Object.getOwnPropertyDescriptor(dom.HTMLScriptElement.prototype,'src')!;
  dom.evaluate();const script=new dom.HTMLScriptElement();script.src=manifest[0]!.path+'?existing=1#part';
  expect(dom.network).toHaveLength(1);expect(new URL(dom.network[0]!.url).searchParams.get('sbarbase_smtp')).toBe(STUDIO_SMTP_ASSET_VERSION);
  expect(new URL(String(script.src)).searchParams.get('existing')).toBe('1');expect(new URL(String(script.src)).hash).toBe('#part');
  expect(dom.calls[0]?.kind).toBe('src');expect(Object.getOwnPropertyDescriptor(dom.HTMLScriptElement.prototype,'src')!.get).toBe(native.get);
  expect(Object.getOwnPropertyDescriptor(dom.HTMLScriptElement.prototype,'src')!.enumerable).toBe(native.enumerable);
 });
 test('bootstrap versions script attributes and both preload assignment orders before network',()=>{
  const dom=cacheDom();dom.evaluate();
  const script=new dom.HTMLScriptElement();script.setAttribute('SRC',manifest[1]!.path);
  const first=new dom.HTMLLinkElement();first.rel='preload';first.as='script';first.href=manifest[2]!.path;
  const second=new dom.HTMLLinkElement();second.href=manifest[3]!.path;second.rel='modulepreload';
  const third=new dom.HTMLLinkElement();third.setAttribute('href',manifest[4]!.path);third.rel='preload';third.as='script';
  expect(dom.network).toHaveLength(4);
  for(const request of dom.network)expect(new URL(request.url).searchParams.get('sbarbase_smtp')).toBe(STUDIO_SMTP_ASSET_VERSION);
  expect(dom.calls.map(call=>call.kind)).toEqual(['attribute','href','href','attribute']);
 });
 test('bootstrap leaves unrelated URLs, API attributes and other-origin base resolution alone',()=>{
  const dom=cacheDom();dom.evaluate();const script=new dom.HTMLScriptElement(),link=new dom.HTMLLinkElement(),element=new dom.Element();
  script.src='https://elsewhere.example.test'+manifest[0]!.path;script.setAttribute('src','/api/config');link.href='/style.css';element.setAttribute('src',manifest[0]!.path);
  script.baseURI='https://elsewhere.example.test/project/default/';script.src=manifest[0]!.path;
  expect(dom.network.map(request=>request.url)).toEqual(['https://elsewhere.example.test'+manifest[0]!.path,'/api/config','/style.css',manifest[0]!.path]);
  expect(element.attributes.src).toBe(manifest[0]!.path);
 });
 test('bootstrap is duplicate-safe and retains original native coercion and errors',()=>{
  const dom=cacheDom();dom.evaluate();const src=Object.getOwnPropertyDescriptor(dom.HTMLScriptElement.prototype,'src')!.set,href=Object.getOwnPropertyDescriptor(dom.HTMLLinkElement.prototype,'href')!.set,attribute=dom.Element.prototype.setAttribute;
  dom.evaluate();expect(Object.getOwnPropertyDescriptor(dom.HTMLScriptElement.prototype,'src')!.set).toBe(src);expect(Object.getOwnPropertyDescriptor(dom.HTMLLinkElement.prototype,'href')!.set).toBe(href);expect(dom.Element.prototype.setAttribute).toBe(attribute);
  const script=new dom.HTMLScriptElement();let coerced=0;const value={toString(){coerced++;return manifest[0]!.path;}};
  script.src=value;expect(dom.calls[0]!.value).toBe(value);expect(coerced).toBe(1);
  expect(()=>{script.src=Symbol('invalid');}).toThrow(TypeError);expect(()=>script.setAttribute('src',Symbol('invalid'))).toThrow(TypeError);
  expect(()=>attribute.call({},'src','/file')).toThrow();
 });
 test('native Turbopack registration resolves its canonical key while network stays versioned',()=>{
  const dom=cacheDom(),original=dom.Element.prototype.getAttribute;dom.evaluate();const script=new dom.HTMLScriptElement();script.src=manifest[0]!.path;
  const expected=manifest[0]!.path;
  // Pinned runtime registerChunk resolves D(t.src); otherChunks waits for the canonical /_next path.
  const registration={src:script.getAttribute('src')};const pending=new Map([[expected,false]]);pending.set(registration.src!,true);
  expect(pending.get(expected)).toBe(true);expect(pending.size).toBe(1);
  expect(new URL(script.src).searchParams.get('sbarbase_smtp')).toBe(STUDIO_SMTP_ASSET_VERSION);
  expect(new URL(original.call(script,'src')!).searchParams.get('sbarbase_smtp')).toBe(STUDIO_SMTP_ASSET_VERSION);
  const getter=dom.Element.prototype.getAttribute;dom.evaluate();expect(dom.Element.prototype.getAttribute).toBe(getter);
 });
 test('canonical registration strips only our parameter from known script attributes',()=>{
  const dom=cacheDom();dom.evaluate();const script=new dom.HTMLScriptElement();script.src=manifest[1]!.path+'?existing=a%20b#part';
  const canonical=new URL(script.getAttribute('SRC')!,dom.origin);expect(canonical.pathname).toBe(manifest[1]!.path);expect(canonical.searchParams.get('existing')).toBe('a b');expect(canonical.hash).toBe('#part');expect(canonical.searchParams.has('sbarbase_smtp')).toBe(false);
  const link=new dom.HTMLLinkElement();link.href=manifest[2]!.path;expect(new URL(link.getAttribute('href')!).searchParams.get('sbarbase_smtp')).toBe(STUDIO_SMTP_ASSET_VERSION);
  script.src='https://elsewhere.example.test'+manifest[0]!.path+'?sbarbase_smtp=other';expect(script.getAttribute('src')).toBe('https://elsewhere.example.test'+manifest[0]!.path+'?sbarbase_smtp=other');
  script.src='/api/config?sbarbase_smtp=other';expect(script.getAttribute('src')).toBe('/api/config?sbarbase_smtp=other');
  expect(script.getAttribute('absent')).toBeNull();expect(()=>script.getAttribute(Symbol('invalid') as unknown as string)).toThrow(TypeError);
 });
});
