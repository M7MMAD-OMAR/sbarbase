import type {EnvironmentRoute} from './handler';

/** A browser's Realtime socket: `/<runtime>/realtime/v1/websocket?apikey=<publishable key>&vsn=…`.
 * The listener asks this before it connects anything: the key must be a live publishable key
 * of that environment, the environment must run Realtime, and the socket then reaches only that
 * environment's own Realtime, with the key swapped for the signed anon token Realtime expects
 * and the Host header naming the tenant, as upstream's gateway does. */
export type RealtimeDecision =
 {ok:true;host:string;port:number;head:string}|
 {ok:false;status:number;message:string};

const PATH=/^\/([a-z][a-z0-9_]{1,30})\/realtime\/v1\/websocket$/;
// Headers the WebSocket handshake needs; everything else stays with the gateway.
const FORWARDED=new Set(['upgrade','connection','sec-websocket-key','sec-websocket-version','sec-websocket-extensions',
 'sec-websocket-protocol','origin','user-agent']);

export function isRealtimeSocket(path:string):boolean {
 return PATH.test(new URL(path,'http://local').pathname);
}

export function realtimeUpgrade(options:{
 route:(runtime:string)=>EnvironmentRoute|undefined|'maintenance';
 verifyKey:(runtime:string,key:string)=>boolean;
}) {
 return (path:string,headers:Headers):RealtimeDecision=>{
  const url=new URL(path,'http://local');
  const runtime=url.pathname.match(PATH)?.[1];
  if(!runtime)return {ok:false,status:404,message:'Unknown route'};
  let route:ReturnType<typeof options.route>;
  try{route=options.route(runtime);}catch{return {ok:false,status:503,message:'Environment routing unavailable'};}
  if(route==='maintenance')return {ok:false,status:503,message:'Environment temporarily paused'};
  if(!route||!route.enabled)return {ok:false,status:404,message:'Unknown environment'};
  if(!route.realtime||!route.realtimeToken)return {ok:false,status:404,message:'Realtime is off for this environment'};
  const key=url.searchParams.get('apikey')??headers.get('apikey');
  let accepted=false;
  try{accepted=!!key&&key.length<=8192&&options.verifyKey(runtime,key);}catch{return {ok:false,status:503,message:'Key verification unavailable'};}
  if(!accepted)return {ok:false,status:401,message:'Invalid API key'};
  let upstream:URL;
  try{upstream=new URL(route.realtime.url);}catch{return {ok:false,status:503,message:'Invalid upstream'};}
  if(upstream.protocol!=='http:'||!/^[a-f0-9]{24}\.realtime$/.test(route.realtime.tenantHost))return {ok:false,status:503,message:'Invalid upstream'};
  const query=new URLSearchParams(url.search);
  query.set('apikey',route.realtimeToken);
  const lines=[`GET /socket/websocket?${query} HTTP/1.1`,`Host: ${route.realtime.tenantHost}`];
  for(const [name,value] of headers)if(FORWARDED.has(name)&&!/[\r\n]/.test(value))lines.push(`${name}: ${value}`);
  return {ok:true,host:upstream.hostname,port:Number(upstream.port||80),head:lines.join('\r\n')+'\r\n\r\n'};
 };
}
