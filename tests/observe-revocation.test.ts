import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {RequestLog} from '../src/gateway/observe';
import {observeHandler,type ContainerReader,type ContainerStats} from '../src/control/observe';

for(const kind of ['logs','metrics'] as const)for(const change of ['removed','viewer'] as const) {
 test(`${kind} rechecks membership after its reader resolves for a ${change} actor`,async()=>{
  const catalog=new Catalog(':memory:');
  try {
   const organization=catalog.createOrganization('owner','A');catalog.setMember('owner',organization,'alice','admin');
   const environment=catalog.createEnvironment('owner',catalog.createProject('owner',organization,'P'),'production');
   const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
   let entered!:()=>void,finish!:()=>void;
   const started=new Promise<void>(resolve=>{entered=resolve;});
   const wait=new Promise<void>(resolve=>{finish=resolve;});
   const reader:ContainerReader={async logs(){entered();await wait;return 'dummy private service log';},
    async stats(){entered();await wait;return [{service:'auth',cpuPercent:1,memoryBytes:2,memoryLimitBytes:3}] as ContainerStats[];}};
   const handler=observeHandler(catalog,async()=> 'alice',new RequestLog(),reader);
   const pending=handler(new Request(`http://local/management/v1/environments/${environment}/${kind}${kind==='logs'?'?source=auth':''}`));
   await started;
   catalog.changeMember('owner',organization,'alice',change==='removed'?null:'viewer');
   finish();
   const response=await pending;
   const admitted=kind==='metrics'&&change==='viewer';
   expect(response.status).toBe(admitted?200:403);
   const body=await response.text();
   if(admitted)expect(body).toContain('memoryBytes');
   else {expect(body).not.toContain('dummy private service log');expect(body).not.toContain('memoryBytes');}
  } finally {catalog.close();}
 });
}
