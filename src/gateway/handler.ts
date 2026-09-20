import {ConcurrencyGate} from './concurrency';
import { timingSafeEqual } from 'node:crypto';

export type EnvironmentRoute = {
  auth: string;
  rest: string;
  storage?: {url:string;tenantHost:string};
  keys: readonly string[];
  anonymousToken: string;
  enabled: boolean;
};
export type RouteRegistry = ReadonlyMap<string, EnvironmentRoute>;
const forwardedHeaders = ['accept','content-type','prefer','range','range-unit','accept-profile','content-profile','x-client-info','x-upsert','cache-control','if-none-match','if-modified-since'];
function matches(a: string, b: string): boolean {
  const left = Buffer.from(a), right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left,right);
}
function error(status:number, message:string) {
  return Response.json({message},{status,headers:{'cache-control':'no-store'}});
}

/** Auth validates user JWTs; PostgREST validates JWTs and applies RLS.
 * This boundary binds an API key to an enabled environment before proxying.
 * Storage uses a trusted tenant header; Realtime, browser CORS and OAuth remain pending.
 */
export function createGateway(registry:RouteRegistry, transport:typeof fetch = fetch, verifyKey?:(environment:string,key:string)=>boolean, bodyReadTimeoutMs=10_000, concurrency=new ConcurrencyGate()) {
  return async (request:Request):Promise<Response> => {
    const url = new URL(request.url);
    const match = url.pathname.match(/^\/([a-z][a-z0-9_]{1,30})\/(auth|rest|storage)\/v1(\/.*)?$/);
    if (!match) return error(404,'Unknown route');
    const environment = match[1], service = match[2], path = match[3] || '/';
    if (!environment || (service !== 'auth' && service !== 'rest' && service !== 'storage')) return error(404,'Unknown route');
    const route = registry.get(environment);
    if (!route || !route.enabled) return error(404,'Unknown route');
    const apiKey = request.headers.get('apikey');
    const readMethod=request.method==='GET'||request.method==='HEAD';
    const signedTokens=url.searchParams.getAll('token');
    const publicStorageRead=service==='storage'&&readMethod&&(
      /^\/object\/public\/[^/]+\/.+/.test(path)||
      /^\/object\/sign\/[^/]+\/.+/.test(path)&&signedTokens.length===1&&
        !!signedTokens[0]&&signedTokens[0].length<=8192);
    // Public object visibility and signed-token validity are enforced by Storage.
    // No exception exists for writes, listing, signing, or authenticated paths.
    if(apiKey!==null) {
      if(!apiKey||apiKey.length>8192) return error(401,'Invalid API key');
      let keyAccepted=false;
      try {keyAccepted=verifyKey?verifyKey(environment,apiKey):route.keys.some(key=>matches(key,apiKey));}
      catch {return error(503,'Key verification unavailable');}
      if(!keyAccepted) return error(401,'Invalid API key');
    } else if(!publicStorageRead) return error(401,'Invalid API key');
    // Never let a forwarded path or absolute URL choose the upstream host.
    if (path.includes('\\') || /%2f|%5c|%00/i.test(path)) return error(400,'Invalid path');
    if (!['GET','HEAD','POST','PUT','PATCH','DELETE'].includes(request.method)) return error(405,'Method not allowed');
    const upstream=service==='storage'?route.storage?.url:route[service];
    if(!upstream) return error(404,'Service not configured');
    let target:URL;
    try {target=new URL(upstream);} catch {return error(503,'Invalid upstream');}
    if (!['http:','https:'].includes(target.protocol) || target.username || target.password) return error(503,'Invalid upstream');
    target.pathname = path;
    target.search = url.search;
    const headers = new Headers();
    for (const name of forwardedHeaders) {
      const value = request.headers.get(name);
      if (value !== null) headers.set(name,value);
    }
    if(service==='storage') {
      const host=route.storage?.tenantHost;
      if(!host||!/^[a-z0-9_][a-z0-9_.-]{0,252}$/.test(host)) return error(503,'Invalid storage tenant');
      headers.set('x-forwarded-host',host);
    }
    const authorization = request.headers.get('authorization');
    if (authorization && !/^Bearer \S+$/i.test(authorization)) return error(401,'Invalid authorization');
    const bearerIsApiKey = authorization?.toLowerCase().startsWith('bearer ') && apiKey!==null && matches(authorization.slice(7), apiKey);
    headers.set('authorization', authorization && !bearerIsApiKey ? authorization : `Bearer ${route.anonymousToken}`);
    return concurrency.run(environment,request,async(signal)=>{
    let body:Uint8Array<ArrayBuffer> | undefined;
    if (request.body) {
      const limit = 1024*1024;
      if (Number(request.headers.get('content-length')) > limit) return error(413,'Request too large');
      const reader=request.body.getReader();
      const chunks:Uint8Array[]=[]; let size=0;
      let timer:ReturnType<typeof setTimeout>|undefined;
      let abortBody=()=>{};
      const aborted=new Promise<never>((_,reject)=>{abortBody=()=>{reject(new Error('Body aborted'));void reader.cancel().catch(()=>{});};});
      request.signal.addEventListener('abort',abortBody,{once:true});
      if(request.signal.aborted)abortBody();
      const expired=new Promise<never>((_,reject)=>{timer=setTimeout(()=>{
        reject(new Error('Body timeout'));void reader.cancel().catch(()=>{});
      },bodyReadTimeoutMs);});
      try {
        while (true) {
          const item=await Promise.race([reader.read(),expired,aborted]);
          if (item.done) break;
          size+=item.value.byteLength;
          if (size>limit) { void reader.cancel().catch(()=>{}); return error(413,'Request too large'); }
          chunks.push(item.value);
        }
      } catch {
        return error(400,'Request body unavailable');
      } finally {clearTimeout(timer);request.signal.removeEventListener('abort',abortBody);reader.releaseLock();}
      body=new Uint8Array(size); let offset=0;
      for(const chunk of chunks) {body.set(chunk,offset);offset+=chunk.length;}
    }
    if(request.signal.aborted)return error(408,'Request cancelled');
    try {
      return await transport(target,{method:request.method,headers,body,redirect:'manual',decompress:false,
        signal:AbortSignal.any([signal,request.signal,AbortSignal.timeout(15_000)])});
    } catch {
      return error(502,'Upstream unavailable');
    }
    });
  };
}
