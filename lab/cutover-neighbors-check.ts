/** Parent owns operation.lock and validates the selected source fence. */
import {serveLocal} from '../src/http/local-server';
import {openUpstreamApplication} from './upstream-app';
const input=await Bun.stdin.json(),app=openUpstreamApplication();
const server=await serveLocal(app.handler),base=`http://127.0.0.1:${server.port}`;
const checks:string[]=[],issued:{runtime:string;id:string}[]=[];
function check(name:string,ok:unknown):asserts ok {if(!ok)throw new Error(name);checks.push(name);}
const resumed:Record<string,number>={};
try {
 const moved=app.catalog.runtimeRouting(input.moved);
 check('moved environment remains paused with target placement',moved.maintenance&&!!moved.placement);
 for(const runtime of input.neighbors as string[]) {
  const state=app.catalog.runtimeRouting(runtime);
  check('neighbor begins in expected maintenance state',state.maintenance&&state.revision===input.expected[runtime]&&state.placement===null);
  const key=app.keys.issue(runtime);issued.push({runtime,id:key.id});
  check('neighbor refused before explicit resume',(await fetch(`${base}/${runtime}/auth/v1/settings`,{headers:{apikey:key.token}})).status===503);
  resumed[runtime]=app.catalog.changeRuntimeRouting(runtime,state.revision,'resume');
  for(const service of ['auth','rest']) {
   const path=service==='auth'?'settings':'';
   const response=await fetch(`${base}/${runtime}/${service}/v1/${path}`,{headers:{apikey:key.token},signal:AbortSignal.timeout(10000)});
   check(`neighbor ${service} works after source restart and routing resume`,response.status===200);await response.arrayBuffer();
  }
  check('moved environment stays paused while neighbor is active',(await fetch(`${base}/${input.moved}/rest/v1/`,{headers:{apikey:key.token}})).status===503);
 }
} finally {
 // Keep active routes only when the complete neighbor check passed.
 if(checks.length!==1+input.neighbors.length*5){
  for(const [runtime,revision] of Object.entries(resumed)){
   if(!app.catalog.runtimeRouting(runtime).maintenance)app.catalog.changeRuntimeRouting(runtime,revision,'pause');
  }
 }
 for(const key of issued)app.keys.revoke(key.runtime,key.id);
 server.stop(true);app.close();
}
await Bun.write('docs/evidence/cutover-neighbor-checks.json',JSON.stringify({scope:'Three source neighbors restarted and resumed through composed gateway. Auth settings and REST root checks, not full neighbor data workloads. Moved environment remains in maintenance with target placement. Temporary keys revoked.',checks,count:checks.length,resumedRevisions:resumed},null,2)+'\n');
console.log(`${checks.length} neighbor restoration checks passed`);
