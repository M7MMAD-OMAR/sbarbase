/** Storage for one environment's Edge Functions (docs/engineering/FUNCTIONS-NETWORK-ISOLATION.md).
 * The shared Storage takes its tenant from `X-Forwarded-Host`, which the caller sets, and a
 * function's code shares its network namespace with the main service, so the tenant cannot be
 * pinned inside the Functions container. Storage is on no environment network; instead the
 * loopback API listens on each environment network's gateway address, admits only sources
 * inside that network's subnet, and forwards every path to Storage with that environment's
 * tenant set here. Storage checks every token itself, so no key is checked here. */

export type FunctionsStorageAt={host:string;port:number;subnet:string};
type StorageEndpoint={url:string;tenantHost:string};

const RUNTIME=/^e_[a-f0-9]{24}$/;
const HOP=new Set(['connection','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','transfer-encoding','upgrade','host','content-length',
 'forwarded','x-forwarded-host','x-forwarded-for','x-forwarded-proto','x-forwarded-port','x-forwarded-prefix']);

function ipv4(address:string):number|undefined {
 const parts=address.split('.');
 if(parts.length!==4||parts.some(part=>!/^(0|[1-9]\d{0,2})$/.test(part)||Number(part)>255))return undefined;
 return parts.reduce((value,part)=>value*256+Number(part),0);
}

/** True when an IPv4 address (or its IPv4-mapped IPv6 form) is inside an IPv4 CIDR subnet. */
export function inSubnet(address:string,subnet:string):boolean {
 const [network,bits,extra]=subnet.split('/');
 const plain=address.replace(/^::ffff:/i,'');
 const at=ipv4(plain),base=ipv4(network??'');
 if(at===undefined||base===undefined||extra!==undefined||!/^\d{1,2}$/.test(bits??'')||Number(bits)>32)return false;
 const size=2**(32-Number(bits));
 return Math.floor(at/size)===Math.floor(base/size);
}

/** The listener addresses the published endpoints ask for: one per environment with Functions on. */
export function functionsStorageWanted(endpoints:Record<string,any>):Map<string,FunctionsStorageAt> {
 const wanted=new Map<string,FunctionsStorageAt>();
 for(const [runtime,entry] of Object.entries(endpoints??{})){
  const at=entry?.functions?.storage;
  if(!RUNTIME.test(runtime)||!at||typeof at.host!=='string'||ipv4(at.host)===undefined||!Number.isInteger(at.port)
   ||typeof at.subnet!=='string'||!inSubnet(at.host,at.subnet))continue;
  wanted.set(runtime,{host:at.host,port:at.port,subnet:at.subnet});
 }
 return wanted;
}

/** Every path to the shared Storage, with this environment's tenant set and the caller's own
 * forwarding headers dropped. */
export function functionsStorage(options:{runtime:string;storage:()=>StorageEndpoint|undefined;active:()=>boolean;transport?:typeof fetch}) {
 const transport=options.transport??fetch;
 return async(request:Request):Promise<Response>=>{
  const storage=options.storage();
  if(!options.active()||!storage||storage.tenantHost!==options.runtime+'.storage.internal')
   return Response.json({message:'Unavailable'},{status:503});
  const url=new URL(request.url),headers=new Headers();
  for(const [name,value] of request.headers)if(!HOP.has(name))headers.set(name,value);
  headers.set('x-forwarded-host',storage.tenantHost);
  const destination=new URL(storage.url);
  destination.pathname=url.pathname;destination.search=url.search;
  const response=await transport(destination,{method:request.method,headers,redirect:'manual',
   ...(['GET','HEAD'].includes(request.method)?{}:{body:request.body,duplex:'half'})} as RequestInit);
  const out=new Headers();
  for(const [name,value] of response.headers)if(!HOP.has(name))out.set(name,value);
  return new Response(response.body,{status:response.status,headers:out});
 };
}

type Listener={stop(force?:boolean):void;drain?(graceMs?:number):Promise<void>};

/** Keeps one listener per environment in step with the published endpoints: opens new ones,
 * and closes those whose environment turned Functions off or whose network changed. */
export function functionsStorageListeners(options:{read:()=>Record<string,any>;
 open:(runtime:string,at:FunctionsStorageAt)=>Promise<Listener>;log?:(line:string)=>void}) {
 const open=new Map<string,{key:string;listener:Listener}>();
 let busy=false,closed=false;
 return {
  async reconcile(){
   // A slow bind must not let the next tick open the same listener twice.
   if(busy||closed)return;busy=true;
   try{await step();}finally{busy=false;}
  },
  runtimes:()=>[...open.keys()],
  /** Opens nothing more and drains every open listener; stop() closes what is left. */
  async drain(graceMs?:number){
   closed=true;
   await Promise.all([...open.values()].map(current=>current.listener.drain?.(graceMs)));
  },
  stop(){closed=true;for(const current of open.values())current.listener.stop(true);open.clear();},
 };
 async function step(){
  let wanted:Map<string,FunctionsStorageAt>;
  try{wanted=functionsStorageWanted(options.read());}catch{return;}
  for(const [runtime,current] of open){
   const at=wanted.get(runtime);
   if(at&&`${at.host}:${at.port}:${at.subnet}`===current.key)continue;
   current.listener.stop(true);open.delete(runtime);
  }
  for(const [runtime,at] of wanted){
   if(open.has(runtime))continue;
   try{
    const listener=await options.open(runtime,at);
    // A stop that began during the bind: the new listener closes at once.
    if(closed){listener.stop(true);return;}
    open.set(runtime,{key:`${at.host}:${at.port}:${at.subnet}`,listener});
   }
   catch(error){options.log?.(`Functions Storage listener for ${runtime} unavailable: ${(error as Error).message}`);}
  }
 }
}
