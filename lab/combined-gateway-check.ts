import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {managementPublishableKey} from './upstream-app';
const server=await Bun.file('.lab/upstream/server.json').json();
const operation=await Bun.file('.lab/upstream/cutover-operation.json').json();
const catalog=new Catalog('.lab/upstream/control.sqlite'),keys=new KeyStore('.secrets/upstream/managed-keys.sqlite');
const checks:string[]=[],issued:{runtime:string;id:string}[]=[];
function check(name:string,ok:unknown):asserts ok {if(!ok)throw new Error(name);checks.push(name);}
try{
 const page=await fetch(server.url+'/');check('foreground console serves built page',page.ok&&(await page.text()).includes('<html'));
 const settings=await fetch(server.url+'/management/auth/v1/settings',{headers:{apikey:managementPublishableKey}});
 check('management identity realm remains reachable alongside moved target',settings.status===200);await settings.arrayBuffer();
 for(const runtime of Object.keys(operation.paused_revisions)){
  check('environment route active in shared control catalog',!catalog.runtimeRouting(runtime).maintenance);
  const key=keys.issue(runtime);issued.push({runtime,id:key.id});
  for(const [service,path] of [['auth','settings'],['rest','']] as const){
   const response=await fetch(`${server.url}/${runtime}/${service}/v1/${path}`,{headers:{apikey:key.token},signal:AbortSignal.timeout(10000)});
   check(`combined ${service} endpoint returns success`,response.status===200);await response.arrayBuffer();
  }
 }
}finally{for(const key of issued)keys.revoke(key.runtime,key.id);catalog.close();keys.close();}
await Bun.write('docs/evidence/combined-gateway-checks.json',JSON.stringify({scope:'Actual foreground supervisor console and management realm, three source neighbors and moved target simultaneously reachable through one gateway. Auth settings/REST root availability, not sustained load or full user workflows.',checks,count:checks.length},null,2)+'\n');
console.log(`${checks.length} combined gateway checks passed`);
