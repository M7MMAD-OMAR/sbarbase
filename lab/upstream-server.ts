import {serveLocal,consolePort} from '../src/http/local-server';
import {uiStatic} from './ui-static';
import {openUpstreamApplication,studioState} from './upstream-app';
import {studioRuntime} from '../src/control/studio';
import {isRealtimeSocket} from '../src/gateway/realtime';
import {readFileSync,unlinkSync} from 'node:fs';

// Local experimental API only. No remote bind or default production exposure.
// A server puts the TLS proxy in front of this listener, and the proxy is started
// with a fixed --upstream, so SBARBASE_CONSOLE_PORT pins the loopback port. Without
// it the port is ephemeral, which suits the lab and breaks a proxy on restart.
const app=openUpstreamApplication();
const server=await serveLocal(async request=>{
 const url=new URL(request.url);
 if(studioRuntime(url.hostname))return app.studio(request);
 if(!['127.0.0.1','localhost'].includes(url.hostname))return new Response('Invalid host',{status:403});
 if(url.pathname==='/favicon.ico')return new Response(null,{status:204});
 return await uiStatic(request)??app.handler(request);
},consolePort(process.env.SBARBASE_CONSOLE_PORT),{hostnames:name=>!!studioRuntime(name),
 // An environment's Realtime socket; Studio hosts never carry one.
 isUpgrade:isRealtimeSocket,upgrade:(path,headers)=>studioRuntime((headers.get('host')??'').replace(/:\d+$/,''))
  ?{ok:false,status:404,message:'Unknown route'}:app.realtime(path,headers)});
// Studio's own server-side calls arrive on the runtime network's gateway address, which
// exists once the runtime is up; the listener opens when the first Studio session records it.
let internal:Awaited<ReturnType<typeof serveLocal>>|undefined,internalAt='';
const studioTimer=setInterval(async()=>{
 const at=studioState().upstream;const want=at?`${at.host}:${at.port}`:'';
 if(want===internalAt)return;
 internal?.stop(true);internal=undefined;internalAt='';
 if(!at)return;
 try{internal=await serveLocal(app.upstream,at.port,{host:at.host});internalAt=want;}
 catch(error){console.error('Studio internal route unavailable:',(error as Error).message);}
},2000);
await Bun.write('.lab/upstream/server.json',JSON.stringify({url:`http://127.0.0.1:${server.port}`,pid:process.pid}));
console.log(`Local Sbarbase API: http://127.0.0.1:${server.port}`);
function stop(){
 clearInterval(studioTimer);internal?.stop(true);server.stop(true);app.close();
 try {if(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')).pid===process.pid)unlinkSync('.lab/upstream/server.json');}catch {}
 process.exit(0);
}
process.on('SIGINT',stop);process.on('SIGTERM',stop);
