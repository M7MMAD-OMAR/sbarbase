import {looksLikeBrowser,SECRET_IN_BROWSER,type EnvironmentRoute,type KeyVerdict} from './handler';
import {routeWithPlacement} from './managed';
import type {RuntimeRouting} from '../control/placement';

/** A Realtime socket: `/<runtime>/realtime/v1/websocket?apikey=<API key>&vsn=…`. The listener asks
 * this before it connects anything: the key must be a live API key of that environment (a secret
 * key only from a server, never a browser), the environment must run Realtime, and the socket then reaches only that
 * environment's own Realtime, with the key swapped for the signed token Realtime expects (anon, or service_role for a secret
 * key; an open socket keeps it until it closes, even when the key is revoked)
 * and the Host header naming the tenant, as upstream's gateway does. */
export type RealtimeDecision =
 {ok:true;host:string;port:number;head:string;onOpen?:()=>()=>void}|
 {ok:false;status:number;message:string};

const PATH=/^\/([a-z][a-z0-9_]{1,30})\/realtime\/v1\/websocket$/;
// Headers the WebSocket handshake needs; everything else stays with the gateway.
const FORWARDED=new Set(['upgrade','connection','sec-websocket-key','sec-websocket-version','sec-websocket-extensions',
 'sec-websocket-protocol','origin','user-agent']);

export function isRealtimeSocket(path:string):boolean {
 return PATH.test(new URL(path,'http://local').pathname);
}

/** The route a new socket gets from the catalog, read on every socket: none until the environment
 * is ready, and 'maintenance' while its routing is paused, by a move or a write pause. */
export function catalogRealtimeRoute(catalog:{runtimeReady(runtime:string):boolean;runtimeRouting(runtime:string):RuntimeRouting},
 resolve:(runtime:string)=>EnvironmentRoute|undefined) {
 return (runtime:string):EnvironmentRoute|undefined|'maintenance'=>{
  if(!catalog.runtimeReady(runtime))return undefined;
  const routing=catalog.runtimeRouting(runtime);
  return routing.maintenance?'maintenance':routeWithPlacement(resolve(runtime),routing);
 };
}

/** On demand Realtime (src/control/on-demand.ts): when the environment saves Realtime as On demand, a socket whose key was verified waits
 * for the service to answer, up to the hold, and is refused with the reason if it does not. */
export type RealtimeWake={isOnDemand(runtime:string):boolean;ensure(runtime:string):Promise<{ok:true}|{ok:false;status:number;message:string}>};

type UpgradeOptions={route:(runtime:string)=>EnvironmentRoute|undefined|'maintenance';verifyKey:(runtime:string,key:string)=>KeyVerdict};
export function realtimeUpgrade(options:UpgradeOptions&{wake?:undefined}):(path:string,headers:Headers)=>RealtimeDecision;
export function realtimeUpgrade(options:UpgradeOptions&{wake:RealtimeWake}):(path:string,headers:Headers)=>RealtimeDecision|Promise<RealtimeDecision>;
export function realtimeUpgrade(options:UpgradeOptions&{wake?:RealtimeWake}) {
 const decide=(path:string,headers:Headers,waited:boolean):RealtimeDecision|Promise<RealtimeDecision>=>{
  const url=new URL(path,'http://local');
  const runtime=url.pathname.match(PATH)?.[1];
  if(!runtime)return {ok:false,status:404,message:'Unknown route'};
  let route:ReturnType<typeof options.route>;
  try{route=options.route(runtime);}catch{return {ok:false,status:503,message:'Environment routing unavailable'};}
  if(route==='maintenance')return {ok:false,status:503,message:'Environment temporarily paused'};
  if(!route||!route.enabled)return {ok:false,status:404,message:'Unknown environment'};
  const onDemand=!waited&&!!options.wake&&options.wake.isOnDemand(runtime);
  if((!route.realtime||!route.realtimeToken)&&!onDemand)return {ok:false,status:404,message:'Realtime is off for this environment'};
  const key=url.searchParams.get('apikey')??headers.get('apikey');
  let verdict:KeyVerdict=null;
  try{verdict=!!key&&key.length<=8192?options.verifyKey(runtime,key):null;}catch{return {ok:false,status:503,message:'Key verification unavailable'};}
  if(verdict!==true&&verdict!=='publishable'&&verdict!=='secret')return {ok:false,status:401,message:'Invalid API key'};
  if(verdict==='secret'&&looksLikeBrowser(headers))return {ok:false,status:401,message:SECRET_IN_BROWSER};
  // The key is verified: only now may a sleeping Realtime be started, and the socket waits for it.
  if(onDemand)return options.wake!.ensure(runtime).then(outcome=>outcome.ok?decide(path,headers,true):{ok:false as const,status:outcome.status,message:outcome.message},
   ()=>({ok:false as const,status:503,message:'Realtime unavailable'}));
  if(!route.realtime||!route.realtimeToken)return {ok:false,status:404,message:'Realtime is off for this environment'};
  const token=verdict==='secret'?route.realtimeServiceToken:route.realtimeToken;
  if(!token)return {ok:false,status:503,message:'Secret keys are unavailable for this environment'};
  let upstream:URL;
  try{upstream=new URL(route.realtime.url);}catch{return {ok:false,status:503,message:'Invalid upstream'};}
  if(upstream.protocol!=='http:'||!/^[a-f0-9]{24}\.realtime$/.test(route.realtime.tenantHost))return {ok:false,status:503,message:'Invalid upstream'};
  const query=new URLSearchParams(url.search);
  query.set('apikey',token);
  const lines=[`GET /socket/websocket?${query} HTTP/1.1`,`Host: ${route.realtime.tenantHost}`];
  for(const [name,value] of headers)if(FORWARDED.has(name)&&!/[\r\n]/.test(value))lines.push(`${name}: ${value}`);
  return {ok:true,host:upstream.hostname,port:Number(upstream.port||80),head:lines.join('\r\n')+'\r\n\r\n'};
 };
 return (path:string,headers:Headers)=>decide(path,headers,false);
}
