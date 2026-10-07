/** Actual process interruption around exact disposable Docker resources and durable Catalog effects. */
import {Database} from 'bun:sqlite';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
import {Catalog} from '../src/control/catalog';
import {enrollLifecycleResources,runLifecycleOperation,type LifecycleAdapter} from '../src/control/lifecycle';
import type {LifecycleBinding,LifecycleResource} from '../src/control/lifecycle-contract';

if(process.env.SBARBASE_LIFECYCLE_VERIFIER!=='1')throw new Error('Assigned lifecycle verifier required');
type Fixture={catalog:string;root:string;state:string;installation:string;environment?:string;runtime:string;
 resources:LifecycleResource[];dockerHost:string;daemon:string;volumeBytes:number};
const admission=(binding:LifecycleBinding)=>binding.coverage==='disposable-fixture'
 ?createHash('sha256').update(JSON.stringify(binding)).digest('hex'):null;
function adapterFor(fixture:Fixture,point?:string):LifecycleAdapter {
 return async input=>{
  const effect=['quarantine','restore','purge'].includes(input.action);
  if(effect&&point===`before:${input.resource.kind}`)process.kill(process.pid,'SIGKILL');
  const child=Bun.spawn(['/usr/bin/python3',resolve('lab/lifecycle_resources.py'),'--root',fixture.root,
   '--installation',fixture.installation,'--state-root',fixture.state,'--docker-host',fixture.dockerHost,
   '--daemon-id',fixture.daemon],{stdin:'pipe',stdout:'pipe',stderr:'pipe'});
  child.stdin.write(JSON.stringify(input));child.stdin.end();
  const [stdout,stderr,code]=await Promise.all([new Response(child.stdout).text(),new Response(child.stderr).text(),child.exited]);
  if(code!==0)throw new Error('Exact Docker adapter refused');
  if(effect&&point===`after:${input.resource.kind}`)process.kill(process.pid,'SIGKILL');
  return JSON.parse(stdout);
 };
}
const config=process.argv[3]!;
const fixture=JSON.parse(readFileSync(config,'utf8')) as Fixture;
if(process.argv[2]==='child'){
 const catalog=new Catalog(fixture.catalog,{lifecycleAdmission:admission});
 try{
  const row=catalog.nextLifecycleOperation();
  if(!row)throw new Error('Missing persisted Docker operation');
  await runLifecycleOperation(catalog,adapterFor(fixture,process.argv[4]),row);
 }finally{catalog.close();}
}else{
 const observations:{id:string;passed:boolean;detail?:unknown}[]=[];
 const record=(id:string,passed:boolean,detail?:unknown)=>{
  observations.push({id,passed,...(detail===undefined?{}:{detail})});
  if(!passed)throw new Error(id);
 };
 async function child(point?:string){
  const process=Bun.spawn([processExec,resolve('lab/lifecycle-docker-journal-check.ts'),'child',config,...(point?[point]:[])],
   {stdout:'pipe',stderr:'pipe'});
  const [stdout,stderr,code]=await Promise.all([new Response(process.stdout).text(),new Response(process.stderr).text(),process.exited]);
  return {code,signal:process.signalCode};
 }
 const processExec=process.execPath;
 let failed=false;
 try{
  const bootstrapCatalog=new Catalog(fixture.catalog,{lifecycleAdmission:admission});
  try{
   bootstrapCatalog.initializeInstallation('bootstrap','fixture-owner','Docker journal installation');
   const org=bootstrapCatalog.createOrganization('fixture-owner','Fixture organization');
   const project=bootstrapCatalog.createProject('fixture-owner',org,'Fixture project');
   fixture.environment=bootstrapCatalog.createEnvironment('fixture-owner',project,'Docker journal target');
   const allocated=bootstrapCatalog.getProvision('fixture-owner',fixture.environment).runtime;
   // Fixture resources were created from this independently generated runtime before Catalog allocation.
   const database=new Database(fixture.catalog);
   database.query('UPDATE provision_jobs SET runtime=? WHERE environment=?').run(fixture.runtime,fixture.environment);
   database.close();
   if(allocated===fixture.runtime)throw new Error('Fixture runtime must be independently allocated');
   const job=bootstrapCatalog.claimProvision()!;bootstrapCatalog.finishProvision(fixture.environment,job.claim!,true);
   writeFileSync(config,JSON.stringify(fixture));
   await enrollLifecycleResources(bootstrapCatalog,adapterFor(fixture),fixture.runtime,fixture.resources,'disposable-fixture');
   bootstrapCatalog.deleteEnvironment('fixture-owner',fixture.environment);
  }finally{bootstrapCatalog.close();}
  for(const point of ['before:container','after:container']){
   const killed=await child(point);
   record(`Docker.quarantine.${point}.SIGKILL`,killed.signal==='SIGKILL',killed);
   const reopened=new Catalog(fixture.catalog,{lifecycleAdmission:admission});
   try{record(`Docker.quarantine.${point}.pending`,reopened.nextLifecycleOperation()!.effects.some(effect=>effect.state==='pending'));}
   finally{reopened.close();}
  }
  record('Docker.quarantine.fresh-process-resume',(await child()).code===0);
  let catalog=new Catalog(fixture.catalog,{lifecycleAdmission:admission});
  try{
   record('Docker.quarantine.retained',catalog.lifecycle('fixture-owner',fixture.environment!)!.state==='deleted');
   catalog.restoreEnvironment('fixture-owner',fixture.environment!,randomUUID());
  }finally{catalog.close();}
  record('Docker.restore.fresh-process',(await child()).code===0);
  catalog=new Catalog(fixture.catalog,{lifecycleAdmission:admission});
  try{
   record('Docker.restore.active',catalog.lifecycle('fixture-owner',fixture.environment!)!.state==='active');
   catalog.deleteEnvironment('fixture-owner',fixture.environment!);
  }finally{catalog.close();}
  record('Docker.quarantine.again',(await child()).code===0);
  const database=new Database(fixture.catalog);
  database.query('UPDATE environment_lifecycle SET retain_until=0 WHERE environment=?').run(fixture.environment!);database.close();
  catalog=new Catalog(fixture.catalog,{lifecycleAdmission:admission});
  try{catalog.purgeEnvironment('fixture-owner',fixture.environment!,randomUUID());}finally{catalog.close();}
  for(const point of ['before:container','after:container','before:volume','after:volume']){
   const killed=await child(point);
   record(`Docker.purge.${point}.SIGKILL`,killed.signal==='SIGKILL',killed);
   const reopened=new Catalog(fixture.catalog,{lifecycleAdmission:admission});
   try{
    const pending=reopened.nextLifecycleOperation()!;
    record(`Docker.purge.${point}.pending`,pending.effects.some(effect=>effect.state==='pending'));
    record(`Docker.purge.${point}.measurements`,pending.effects.every(effect=>Number.isSafeInteger(effect.reclaimed_bytes)&&effect.reclaimed_bytes>=0));
   }finally{reopened.close();}
  }
  record('Docker.purge.fresh-process-resume',(await child()).code===0);
  catalog=new Catalog(fixture.catalog,{lifecycleAdmission:admission});
  try{
   const final=catalog.lifecycle('fixture-owner',fixture.environment!)!;
   record('Docker.purge.settled',final.state==='purged'&&final.effects.length===2&&final.effects.every(effect=>effect.state==='done'));
   record('Docker.purge.measured-reclaim',final.effects.reduce((sum,effect)=>sum+effect.reclaimed_bytes,0)>0,
    {effects:final.effects.map(effect=>({resource:effect.resource,bytes:effect.reclaimed_bytes}))});
   const volume=fixture.resources.find(resource=>resource.kind==='volume')!;
   record('Docker.purge.volume-measurement-preserved',final.effects.find(effect=>effect.resource===volume.resource)?.reclaimed_bytes===fixture.volumeBytes,
    {expected:fixture.volumeBytes,actual:final.effects.find(effect=>effect.resource===volume.resource)?.reclaimed_bytes});
  }finally{catalog.close();}
 }catch(error){
  failed=true;
  if(!observations.some(item=>!item.passed))observations.push({id:'Docker.journal.execution',passed:false,
   detail:error instanceof Error?error.message:'Docker journal execution refused'});
 }
 finally{
  mkdirSync('/evidence',{recursive:true});
  writeFileSync('/evidence/actual-docker-journal.json',JSON.stringify({contract:'SB-07/disposable-docker-journal/v1',observations,
   limitations:['Actual worker SIGKILL and Docker effects; fixture-only admission does not grant SB-05 or application acceptance.']},null,2)+'\n');
 }
 const failures=observations.filter(item=>!item.passed).length;
 console.log(JSON.stringify({total:observations.length,failed:failures,skipped:0}));
 if(failed)process.exitCode=1;
}
