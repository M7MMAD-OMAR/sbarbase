import http from 'node:http';
import https from 'node:https';
import {isIP} from 'node:net';
import {Resolver} from 'node:dns/promises';

/** Outgoing HTTP for `net.http_*` (docs/engineering/SCHEDULED-JOBS.md, "Outgoing HTTP").
 *
 * A request is performed by the scheduler on the server itself, so the policy is the boundary between a
 * tenant's SQL and everything the server can reach: loopback, the Docker networks, other environments'
 * services, the cloud's metadata address. The name is resolved once, every answer is checked, and the
 * connection goes to the checked address with the original name for TLS and `Host`. Each redirect is
 * checked again from the start. */

type Range={v:4|6;base:bigint;bits:number;label:string};

function v4(text:string):bigint|null {
 const parts=text.split('.');
 if(parts.length!==4||parts.some(part=>!/^(0|[1-9][0-9]{0,2})$/.test(part)||Number(part)>255))return null;
 return parts.reduce((value,part)=>(value<<8n)|BigInt(part),0n);
}

function v6(text:string):bigint|null {
 let value=text.toLowerCase();
 const zone=value.indexOf('%');if(zone>=0)value=value.slice(0,zone);
 let tail:bigint|null=null;
 const lastColon=value.lastIndexOf(':');
 if(value.includes('.')){
  tail=v4(value.slice(lastColon+1));if(tail===null)return null;
  value=value.slice(0,lastColon+1)+'0:0';
 }
 const halves=value.split('::');
 if(halves.length>2)return null;
 const head=halves[0]?halves[0].split(':'):[],rest=halves.length===2&&halves[1]?halves[1].split(':'):[];
 if([...head,...rest].some(group=>!/^[0-9a-f]{1,4}$/.test(group)))return null;
 const missing=8-head.length-rest.length;
 if(halves.length===1?missing!==0:missing<1)return null;
 const groups=[...head,...Array(halves.length===2?missing:0).fill('0'),...rest];
 let result=groups.reduce((acc,group)=>(acc<<16n)|BigInt(parseInt(group,16)),0n);
 if(tail!==null)result=(result&~0xffffffffn)|tail;
 return result;
}

/** An address as {v, value}, or null when it is not an IP address. */
export function parseAddress(text:string):{v:4|6;value:bigint}|null {
 const plain=text.startsWith('[')&&text.endsWith(']')?text.slice(1,-1):text;
 const four=v4(plain);if(four!==null)return {v:4,value:four};
 if(!plain.includes(':'))return null;
 const six=v6(plain);return six===null?null:{v:6,value:six};
}

function range(cidr:string,label:string):Range {
 const [address,size]=cidr.split('/');
 const parsed=parseAddress(address!);
 if(!parsed)throw new Error('Invalid range '+cidr);
 const bits=Number(size??(parsed.v===4?32:128)),width=parsed.v===4?32:128;
 const mask=bits===0?0n:((1n<<BigInt(bits))-1n)<<BigInt(width-bits);
 return {v:parsed.v,base:parsed.value&mask,bits,label};
}

function inside(entry:Range,address:{v:4|6;value:bigint}) {
 if(entry.v!==address.v)return false;
 const width=entry.v===4?32:128;
 const mask=entry.bits===0?0n:((1n<<BigInt(entry.bits))-1n)<<BigInt(width-entry.bits);
 return (address.value&mask)===entry.base;
}

/** The IANA IPv4 and IPv6 special-purpose registries, and the ranges that carry an IPv4 address. */
const SPECIAL:Range[]=[
 ['0.0.0.0/8','this network'],['10.0.0.0/8','private'],['100.64.0.0/10','shared address space'],['127.0.0.0/8','loopback'],
 ['169.254.0.0/16','link-local'],['172.16.0.0/12','private'],['192.0.0.0/24','IETF protocol assignments'],['192.0.2.0/24','documentation'],
 ['192.88.99.0/24','6to4 relay'],['192.168.0.0/16','private'],['198.18.0.0/15','benchmarking'],['198.51.100.0/24','documentation'],
 ['203.0.113.0/24','documentation'],['224.0.0.0/4','multicast'],['240.0.0.0/4','reserved'],
 ['::/128','unspecified'],['::1/128','loopback'],['::/96','IPv4 compatible'],['64:ff9b::/96','NAT64'],['64:ff9b:1::/48','NAT64'],
 ['100::/64','discard'],['2001::/23','IETF protocol assignments'],['2001:db8::/32','documentation'],['2002::/16','6to4'],
 ['fc00::/7','unique local'],['fe80::/10','link-local'],['fec0::/10','site-local'],['ff00::/8','multicast'],
].map(([cidr,label])=>range(cidr!,label!));
const MAPPED=range('::ffff:0:0/96','IPv4 mapped');

export type Policy={
 /** Ports allowed besides the scheme's own when listed (80 and 443 by default). */
 ports:Set<number>;
 /** The installation's public origin, `https://api.example.com` or with a port, or null. */
 publicOrigin:string|null;
 /** Every address of the server itself, and every Docker network's subnet. */
 own:Range[];
};

export function policy(input:{ports?:number[];publicUrl?:string|null;addresses?:string[];subnets?:string[]}):Policy {
 const own:Range[]=[];
 for(const address of input.addresses??[]){const parsed=parseAddress(address);if(parsed)own.push({v:parsed.v,base:parsed.value,bits:parsed.v===4?32:128,label:'this server'});}
 for(const subnet of input.subnets??[]){try{own.push({...range(subnet,'a Docker network')});}catch{}}
 let publicOrigin:string|null=null;
 if(input.publicUrl){try{const url=new URL(input.publicUrl);if(url.protocol==='http:'||url.protocol==='https:')publicOrigin=url.origin;}catch{}}
 return {ports:new Set(input.ports?.length?input.ports:[80,443]),publicOrigin,own};
}

/** Why this address may not be reached, or null. `publicOrigin` lifts the server's own addresses only. */
export function refusal(address:string,rules:Policy,publicOrigin=false):string|null {
 let parsed=parseAddress(address);
 if(!parsed)return 'not an IP address';
 if(parsed.v===6&&inside(MAPPED,parsed))parsed={v:4,value:parsed.value&0xffffffffn};
 for(const entry of SPECIAL)if(inside(entry,parsed))return entry.label+' address';
 if(!publicOrigin)for(const entry of rules.own)if(inside(entry,parsed))return entry.label;
 return null;
}

export type HttpRequest={method:'GET'|'POST'|'DELETE';url:string;headers:Record<string,string>;body:Uint8Array|null;timeoutMs:number};
export type HttpResult={status:number;headers:Record<string,string>;contentType:string|null;body:string|null;error:string|null}
 |{status:null;timedOut:boolean;error:string};

export const MAX_BODY=1024*1024,MAX_REDIRECTS=5,MAX_TIMEOUT_MS=60_000,LOOKUP_MS=5_000;
export type NameResolver=(host:string)=>Promise<string[]>;

/** Names through c-ares, which waits on sockets with its own timeout, never on the process-wide thread pool
 * getaddrinfo uses: one tenant's slow name servers cannot hold up another environment's lookups. /etc/hosts is
 * not read, so `localhost` and the server's own names resolve only as public DNS answers them. */
export function systemResolver():NameResolver {
 const resolver=new Resolver({timeout:2000,tries:2});
 return async host=>{
  const [four,six]=await Promise.allSettled([resolver.resolve4(host),resolver.resolve6(host)]);
  const addresses=[...(four.status==='fulfilled'?four.value:[]),...(six.status==='fulfilled'?six.value:[])];
  if(addresses.length)return addresses;
  const reason=(four.status==='rejected'?four.reason:six.status==='rejected'?six.reason:null) as {code?:string}|null;
  throw Object.assign(new Error('ENOTFOUND'),{code:reason?.code==='ETIMEOUT'?'EAI_AGAIN':'ENOTFOUND'});
 };
}

class Refused extends Error {}
class TimedOut extends Error {}

function withTimeout<T>(promise:Promise<T>,ms:number,make:()=>Error):Promise<T> {
 let timer:ReturnType<typeof setTimeout>|undefined;
 return Promise.race([promise,new Promise<T>((_,reject)=>{timer=setTimeout(()=>reject(make()),ms);})]).finally(()=>clearTimeout(timer));
}

/** The checked address for this URL, or a Refused error. */
async function target(url:URL,rules:Policy,resolve:NameResolver,budget:number,refuse:typeof refusal):Promise<string> {
 if(url.protocol!=='http:'&&url.protocol!=='https:')throw new Refused('only http and https are allowed');
 if(url.username||url.password)throw new Refused('credentials in the address are not allowed');
 const port=url.port?Number(url.port):url.protocol==='https:'?443:80;
 const exact=rules.publicOrigin!==null&&url.origin===rules.publicOrigin;
 if(!exact&&!rules.ports.has(port))throw new Refused(`port ${port} is not allowed`);
 const host=url.hostname.startsWith('[')?url.hostname.slice(1,-1):url.hostname;
 if(!host)throw new Refused('no host');
 const addresses=isIP(host)?[host]:await withTimeout(resolve(host),Math.min(LOOKUP_MS,budget),()=>new TimedOut('lookup'));
 if(!addresses.length)throw new Error('ENOTFOUND');
 for(const address of addresses){const why=refuse(address,rules,exact);if(why)throw new Refused(`${host} resolves to a ${why}`);}
 return addresses[0]!;
}

function once(request:HttpRequest,url:URL,address:string,signal:AbortSignal):Promise<{status:number;raw:string[];body:Buffer;cut:boolean}> {
 return new Promise((resolve,reject)=>{
  const module=url.protocol==='https:'?https:http;
  const host=url.hostname.startsWith('[')?url.hostname.slice(1,-1):url.hostname;
  const family=isIP(address)===6?6:4;
  const body=request.body&&request.body.length?Buffer.from(request.body):null;
  const headers:Record<string,string>={...request.headers};
  if(body&&!Object.keys(headers).some(key=>key.toLowerCase()==='content-length'))headers['Content-Length']=String(body.length);
  const outgoing=module.request({protocol:url.protocol,hostname:host,port:url.port||undefined,path:url.pathname+url.search,method:request.method,
   headers,agent:false,signal,servername:isIP(host)?undefined:host,
   lookup:(_name:string,options:{all?:boolean},callback:(...args:unknown[])=>void)=>{
    if(options?.all)callback(null,[{address,family}]);else callback(null,address,family);
   }} as https.RequestOptions,response=>{
   const chunks:Buffer[]=[];let size=0,cut=false;
   response.on('data',(chunk:Buffer)=>{
    if(cut)return;
    if(size+chunk.length>MAX_BODY){chunks.push(chunk.subarray(0,MAX_BODY-size));size=MAX_BODY;cut=true;response.destroy();resolve({status:response.statusCode??0,raw:response.rawHeaders,body:Buffer.concat(chunks),cut});return;}
    chunks.push(chunk);size+=chunk.length;
   });
   response.on('end',()=>resolve({status:response.statusCode??0,raw:response.rawHeaders,body:Buffer.concat(chunks),cut}));
   response.on('error',reject);
  });
  outgoing.on('error',reject);
  if(body)outgoing.write(body);
  outgoing.end();
 });
}

function message(error:unknown):string {
 const code=String((error as {code?:unknown})?.code??(error as Error)?.message??'');
 if(/ENOTFOUND|EAI_AGAIN|EAI_NONAME/.test(code))return "Couldn't resolve host name";
 if(/ECONNREFUSED|EHOSTUNREACH|ENETUNREACH|ECONNRESET/.test(code))return "Couldn't connect to server";
 if(/CERT|SSL|TLS|self.signed|unable to verify/i.test(code))return 'SSL peer certificate or SSH remote key was not OK';
 if(/ERR_INVALID_HTTP_TOKEN|ERR_INVALID_CHAR|header/i.test(code))return 'Invalid request header';
 return 'Failed sending or receiving data';
}

/** Perform one queued request under the policy, following at most five redirects, within its timeout.
 * `refuse` is the address check; only the unit tests pass another, to reach a loopback test server. */
export async function perform(request:HttpRequest,rules:Policy,resolve:NameResolver=systemResolver(),refuse:typeof refusal=refusal):Promise<HttpResult> {
 const timeoutMs=Math.max(1,Math.min(MAX_TIMEOUT_MS,Math.trunc(request.timeoutMs)||5000));
 const deadline=Date.now()+timeoutMs;
 const controller=new AbortController();
 const timer=setTimeout(()=>controller.abort(),timeoutMs);
 let current={...request};
 let url:URL;
 try{url=new URL(request.url);}catch{clearTimeout(timer);return {status:null,timedOut:false,error:'URL using bad/illegal format or missing URL'};}
 try {
  for(let hop=0;;hop++){
   const address=await withTimeout(target(url,rules,resolve,deadline-Date.now(),refuse),Math.max(1,deadline-Date.now()),()=>new TimedOut('request'));
   const answer=await withTimeout(once(current,url,address,controller.signal),Math.max(1,deadline-Date.now()),()=>new TimedOut('request'));
   const location=[301,302,303,307,308].includes(answer.status)?header(answer.raw,'location'):null;
   if(location!==null){
    if(hop>=MAX_REDIRECTS)return {status:null,timedOut:false,error:`Maximum (${MAX_REDIRECTS}) redirects followed`};
    const next=new URL(location,url);
    if(url.protocol==='https:'&&next.protocol==='http:')throw new Refused('a redirect from https to http is not allowed');
    const headers={...current.headers};
    if(next.origin!==url.origin)for(const key of Object.keys(headers))if(['authorization','cookie'].includes(key.toLowerCase()))delete headers[key];
    const toGet=answer.status===303||((answer.status===301||answer.status===302)&&current.method==='POST');
    current={...current,headers,method:toGet?'GET':current.method,body:toGet?null:current.body};
    url=next;continue;
   }
   const headers:Record<string,string>={};
   for(let i=0;i+1<answer.raw.length;i+=2)headers[answer.raw[i]!]=answer.raw[i+1]!;
   const text=answer.body.length?answer.body.toString('utf8').replaceAll('\u0000',''):null;
   return {status:answer.status,headers,contentType:header(answer.raw,'content-type'),body:text,
    error:answer.cut?`Response body cut at ${MAX_BODY/1024/1024} MiB`:null};
  }
 } catch(error) {
  if(error instanceof Refused)return {status:null,timedOut:false,error:`Refused by the server's outgoing request policy: ${error.message}`};
  if(error instanceof TimedOut||controller.signal.aborted||Date.now()>=deadline)
   return {status:null,timedOut:true,error:`Timeout of ${timeoutMs} ms reached`};
  return {status:null,timedOut:false,error:message(error)};
 } finally {
  clearTimeout(timer);
  controller.abort();
 }
}

function header(raw:string[],name:string):string|null {
 let found:string|null=null;
 for(let i=0;i+1<raw.length;i+=2)if(raw[i]!.toLowerCase()===name)found=raw[i+1]!;
 return found;
}
