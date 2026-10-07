import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {LifecycleAuthorizationStore} from '../src/control/lifecycle-authority';
import {parseArgs} from 'node:util';
import {installedLifecycleAdmission} from '../src/control/lifecycle-recovery';
import {resolveRuntimePlacement,placementServices} from '../src/control/placement';
import {Catalog} from '../src/control/catalog';
import {enrollLifecycleResources,runLifecycleOperation,type LifecycleAdapter} from '../src/control/lifecycle';
import {validateLifecycleCoverage,type LifecycleResource} from '../src/control/lifecycle-contract';

if(process.env.SBARBASE_LIFECYCLE_WORKER_LOCKED!=='1')throw new Error('Use python3 lab/lifecycle-worker.py');
const {values}=parseArgs({options:{catalog:{type:'string'},root:{type:'string'},installation:{type:'string'},
 'docker-host':{type:'string'},'daemon-id':{type:'string'},'state-root':{type:'string'},checkout:{type:'string'},
 'recovery-receipt':{type:'string'},enroll:{type:'string'},runtime:{type:'string'},coverage:{type:'string'},'migrate-shared':{type:'boolean'},'migrate-services':{type:'boolean'},'runtime-secrets':{type:'string'}}});
for(const required of ['catalog','root','installation','docker-host','daemon-id','state-root','checkout'] as const)
 if(!values[required])throw new Error('Missing worker configuration');
const checkout=values.checkout!;
const adapter:LifecycleAdapter=async input=>{
 const child=Bun.spawn(['/usr/bin/python3',checkout+'/lab/lifecycle_resources.py','--root',values.root!,
  '--installation',values.installation!,'--docker-host',values['docker-host']!,'--daemon-id',values['daemon-id']!,
  '--state-root',values['state-root']!,'--catalog',values.catalog!],{stdin:'pipe',stdout:'pipe',stderr:'pipe'});
 child.stdin.write(JSON.stringify(input));child.stdin.end();
 const [output,error,code]=await Promise.all([new Response(child.stdout).text(),new Response(child.stderr).text(),child.exited]);
 if(code!==0)throw new Error('Resource adapter refused');
 const result=JSON.parse(output);
 if(!result||typeof result!=='object'||!['present','absent','quarantined','restored','purged'].includes(result.outcome))
  throw new Error('Invalid resource adapter result');
 return result;
};
const admission=values['recovery-receipt']?installedLifecycleAdmission(checkout,values['recovery-receipt']):undefined;
const catalog=new Catalog(values.catalog!,{lifecycleAdmission:admission});
// Only original installation paths select the retained native management process.
if(resolve(values['state-root']!)!==resolve(checkout,'.lab/upstream')||
 resolve(values.catalog!)!==resolve(checkout,'.lab/upstream/control.sqlite'))throw new Error('Original lifecycle installation binding differs');
catalog.lifecycleAuthorizationStore=LifecycleAuthorizationStore.installed(checkout);
try {
 if(values.enroll){
  if(!values.runtime||!values.coverage)throw new Error('Enrollment requires runtime and coverage');
  const resources=JSON.parse(readFileSync(values.enroll,'utf8')) as LifecycleResource[];
  validateLifecycleCoverage(values.runtime,resources,values.coverage);
  if(values['migrate-shared']){
   for(const resource of resources.filter(item=>item.kind==='shared-database')){
    const files=resources.find(item=>item.kind==='directory'&&item.resource===resource.identity?.files.resource);
    if(!files)throw new Error('Shared migration requires exact paired tenant files');
    await adapter({action:'migrate-shared',resource,files,runtime:values.runtime,epoch:catalog.runtimeEpoch(values.runtime),operation:resource.resource});
   }
  }
  if(values['migrate-services']){
   if(!values['runtime-secrets'])throw new Error('Legacy service migration requires explicit private runtime configuration');
   const shared=resources.find(item=>item.kind==='shared-database');
   if(!shared)throw new Error('Legacy service migration requires exact shared database ownership');
   for(const resource of resources.filter(item=>item.kind==='container'&&item.legacy)){
    await adapter({action:'migrate-service',resource,shared,runtimeSecrets:values['runtime-secrets'],runtime:values.runtime,epoch:catalog.runtimeEpoch(values.runtime),operation:resource.resource});
   }
  }
  await enrollLifecycleResources(catalog,adapter,values.runtime,resources,values.coverage);
 }else{
  const row=catalog.nextLifecycleOperation();
  if(row)await runLifecycleOperation(catalog,adapter,row,async current=>{
   try{
    const placement=resolveRuntimePlacement(current.runtime,catalog.runtimeRouting(current.runtime));
    placementServices(placement);
    const endpoints=JSON.parse(readFileSync(values['state-root']+'/endpoints.json','utf8'))[current.runtime];
    if(!endpoints?.auth||!endpoints?.rest||!endpoints?.storage?.url)return false;
    for(const [base,path] of [[endpoints.auth,'/health'],[endpoints.rest,'/'],[endpoints.storage.url,'/status']] as const){
     const url=new URL(path,base);
     if(!['http:','https:'].includes(url.protocol)||url.username||url.password)return false;
     const response=await fetch(url,{redirect:'error',signal:AbortSignal.timeout(5000)});
     if(!response.ok)return false;await response.body?.cancel();
    }
    return true;
   }catch{return false;}
  });
 }
}finally{catalog.close();}
