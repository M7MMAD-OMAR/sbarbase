/** Actual SIGKILL and SQLite replay drill inside the assigned public verifier. */
import {Database} from 'bun:sqlite';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,statSync,existsSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
import {Catalog} from '../src/control/catalog';
import {enrollLifecycleResources,runLifecycleOperation,type LifecycleAdapter} from '../src/control/lifecycle';
import type {LifecycleBinding,LifecycleResource} from '../src/control/lifecycle-contract';

if(process.env.SBARBASE_LIFECYCLE_VERIFIER!=='1')throw new Error('Assigned lifecycle verifier required');
type Fixture={catalog:string;root:string;state:string;installation:string;environment:string;runtime:string;resource:LifecycleResource};
const admission=(binding:LifecycleBinding)=>binding.coverage==='disposable-fixture'
 ?createHash('sha256').update(JSON.stringify(binding)).digest('hex'):null;
function adapterFor(fixture:Fixture,crash?:string):LifecycleAdapter {
 return async input=>{
  const effect=['quarantine','restore','purge'].includes(input.action);
  if(effect&&crash==='before')process.kill(process.pid,'SIGKILL');
  const child=Bun.spawn(['/usr/bin/python3',resolve('lab/lifecycle_resources.py'),'--root',fixture.root,
   '--installation',fixture.installation,'--state-root',fixture.state],{stdin:'pipe',stdout:'pipe',stderr:'pipe'});
  child.stdin.write(JSON.stringify(input));child.stdin.end();
  const [stdout,stderr,code]=await Promise.all([new Response(child.stdout).text(),new Response(child.stderr).text(),child.exited]);
  if(code!==0)throw new Error('Real resource adapter refused');
  if(effect&&crash==='after')process.kill(process.pid,'SIGKILL');
  return JSON.parse(stdout);
 };
}
if(process.argv[2]==='child'){
 const fixture=JSON.parse(readFileSync(process.argv[3]!,'utf8')) as Fixture;
 const catalog=new Catalog(fixture.catalog,{lifecycleAdmission:admission});
 try{
  const row=catalog.nextLifecycleOperation();
  if(!row)throw new Error('Missing persisted operation');
  await runLifecycleOperation(catalog,adapterFor(fixture,process.argv[4]),row);
 }finally{catalog.close();}
}else{
 const observations:{id:string;passed:boolean;detail?:unknown}[]=[];
 const record=(id:string,passed:boolean,detail?:unknown)=>{
  observations.push({id,passed,...(detail===undefined?{}:{detail})});
  if(!passed)throw new Error(id);
 };
 const bases:string[]=[];
 let failed=false;
 try{
  for(const phase of ['quarantine','purge'] as const)for(const crash of ['before','after'] as const){
   const base=mkdtempSync(join(tmpdir(),'sb07-journal-'));bases.push(base);
   const root=join(base,'resources'),state=join(base,'state');mkdirSync(root);mkdirSync(state);
   const installation=randomUUID();const catalogPath=join(base,'catalog.sqlite');
   const catalog=new Catalog(catalogPath,{lifecycleAdmission:admission});
   catalog.initializeInstallation('bootstrap','fixture-owner','Fixture installation');
   const org=catalog.createOrganization('fixture-owner','Fixture organization');
   const project=catalog.createProject('fixture-owner',org,'Fixture project');
   const environment=catalog.createEnvironment('fixture-owner',project,'Fixture target');
   const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
   const runtime=catalog.getProvision('fixture-owner',environment).runtime;
   const resourceId=randomUUID(),path=join(root,runtime,resourceId);mkdirSync(path,{recursive:true});
   const marker='.sbarbase-lifecycle-owner.json';
   writeFileSync(join(path,marker),JSON.stringify({installation,runtime,resource:resourceId}));
   writeFileSync(join(path,'payload'),Buffer.alloc(65536,7));
   const neighbor=join(root,'neighbor');mkdirSync(neighbor);writeFileSync(join(neighbor,'payload'),'preserved neighbor');
   const before=createHash('sha256').update(readFileSync(join(neighbor,'payload'))).digest('hex');
   const identity=statSync(path);
   const resource:LifecycleResource={kind:'directory',id:path,resource:resourceId,installation,runtime,
    device:identity.dev,inode:identity.ino,marker};
   const fixture:Fixture={catalog:catalogPath,root,state,installation,environment,runtime,resource};
   const config=join(base,'fixture.json');writeFileSync(config,JSON.stringify(fixture));
   await enrollLifecycleResources(catalog,adapterFor(fixture),runtime,[resource],'disposable-fixture');
   catalog.deleteEnvironment('fixture-owner',environment);
   if(phase==='purge'){
    await runLifecycleOperation(catalog,adapterFor(fixture),catalog.nextLifecycleOperation()!);
    const database=new Database(catalogPath);database.query('UPDATE environment_lifecycle SET retain_until=0 WHERE environment=?').run(environment);database.close();
    catalog.purgeEnvironment('fixture-owner',environment,randomUUID());
   }
   catalog.close();
   const killed=Bun.spawn([process.execPath,resolve('lab/lifecycle-journal-check.ts'),'child',config,crash],
    {stdout:'pipe',stderr:'pipe',env:{...process.env,SBARBASE_LIFECYCLE_VERIFIER:'1'}});
   const [killOutput,killError,killCode]=await Promise.all([new Response(killed.stdout).text(),new Response(killed.stderr).text(),killed.exited]);
   record(`${phase}.${crash}.SIGKILL`,killed.signalCode==='SIGKILL',{exitCode:killCode,signal:killed.signalCode});
   const reopened=new Catalog(catalogPath,{lifecycleAdmission:admission});
   const pending=reopened.nextLifecycleOperation();
   record(`${phase}.${crash}.durable-intent`,!!pending&&pending.effects.some(effect=>effect.state==='pending'));
   reopened.close();
   const resumed=Bun.spawn([process.execPath,resolve('lab/lifecycle-journal-check.ts'),'child',config],
    {stdout:'pipe',stderr:'pipe',env:{...process.env,SBARBASE_LIFECYCLE_VERIFIER:'1'}});
   const [resumeOutput,resumeError,resumeCode]=await Promise.all([new Response(resumed.stdout).text(),new Response(resumed.stderr).text(),resumed.exited]);
   record(`${phase}.${crash}.fresh-process-resume`,resumeCode===0);
   const settled=new Catalog(catalogPath,{lifecycleAdmission:admission});
   const final=settled.lifecycle('fixture-owner',environment)!;
   record(`${phase}.${crash}.postcondition`,final.state===(phase==='purge'?'purged':'deleted')&&
    final.effects.length===1&&final.effects[0]!.state==='done');
   record(`${phase}.${crash}.neighbor-preserved`,createHash('sha256').update(readFileSync(join(neighbor,'payload'))).digest('hex')===before);
   if(phase==='purge'){
    record(`${phase}.${crash}.resource-absent`,!existsSync(path));
    record(`${phase}.${crash}.reclaim-measured`,final.effects[0]!.reclaimed_bytes>=65536,{bytes:final.effects[0]!.reclaimed_bytes});
   }else{
    record(`${phase}.${crash}.retained-bytes`,readFileSync(join(path,'payload')).equals(Buffer.alloc(65536,7)));
    const restore=settled.restoreEnvironment('fixture-owner',environment,randomUUID());
    await runLifecycleOperation(settled,adapterFor(fixture),restore);
    record(`${phase}.${crash}.restore`,settled.lifecycle('fixture-owner',environment)!.state==='active');
   }
   settled.close();
  }
 }catch(error){
  failed=true;
  if(!observations.some(item=>!item.passed))observations.push({id:'journal.execution',passed:false,
   detail:error instanceof Error?error.message:'Journal execution refused'});
 }finally{
  mkdirSync('/evidence',{recursive:true});
  writeFileSync('/evidence/actual-journal.json',JSON.stringify({contract:'SB-07/disposable-journal/v1',observations,
   limitations:['Real process SIGKILL and filesystem effects on disposable fixtures; no complete application or SB-05 acceptance.']},null,2)+'\n');
  for(const base of bases)rmSync(base,{recursive:true,force:true});
 }
 const failures=observations.filter(item=>!item.passed).length;
 console.log(JSON.stringify({total:observations.length,failed:failures,skipped:0}));
 if(failed)process.exitCode=1;
}
