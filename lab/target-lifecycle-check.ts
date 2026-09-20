import {serveLocal} from '../src/http/local-server';
import {openUpstreamApplication} from './upstream-app';
const input=await Bun.stdin.json(),app=openUpstreamApplication(),server=await serveLocal(app.handler);
const key=app.keys.issue(input.environment);const checks:string[]=[];
try{
 for(const [service,path] of [['auth','settings'],['rest','']] as const){
  const response=await fetch(`http://127.0.0.1:${server.port}/${input.environment}/${service}/v1/${path}`,{headers:{apikey:key.token},signal:AbortSignal.timeout(10000)});
  await response.arrayBuffer();
  if(response.status!==(input.running?200:503))throw new Error('Unexpected lifecycle availability');
  checks.push(`${service} ${input.running?'available':'paused'} through composed gateway`);
 }
}finally{app.keys.revoke(input.environment,key.id);server.stop(true);app.close();}
console.log(JSON.stringify(checks));
