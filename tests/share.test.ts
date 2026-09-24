import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {shareHandler} from '../src/control/share';
import {ConcurrencyGate} from '../src/gateway/concurrency';
import {GATEWAY} from '../src/gateway/shares';

/** The bootstrap organization makes `alice` an installation operator; `bob` owns a client organization. */
function clients() {
 const catalog=new Catalog(':memory:');
 const operators=catalog.initializeInstallation('op-1','alice','Operators');
 const client=catalog.createOrganization('alice','Client');catalog.setMember('alice',client,'bob','owner');catalog.setMember('alice',client,'carol','viewer');
 const project=catalog.createProject('alice',client,'Shop');
 const ready=Array.from({length:4},(_,k)=>{const environment=catalog.createEnvironment('alice',project,'env'+k),job=catalog.claimProvision()!;
  catalog.finishProvision(environment,job.claim!,true);return {environment,runtime:job.runtime};});
 const handler=shareHandler(catalog,async request=>request.headers.get('authorization'));
 const call=(environment:string,actor:string,method='GET',body?:unknown)=>handler(new Request(
  `http://local/management/v1/environments/${environment}/share`,{method,headers:{authorization:actor,
   ...(body===undefined?{}:{'content-type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)})}));
 return {catalog,operators,ready,call};
}

test('a client reads its share but not the installation, and cannot change it',async()=>{
 const {catalog,ready,call}=clients(),[shop]=ready;
 for(const actor of ['bob','carol'])
  expect((await (await call(shop!.environment,actor)).json()).data).toEqual({share:8,default:8,ceiling:GATEWAY.ceiling,operator:false});
 expect((await call(shop!.environment,'bob','PUT',{share:4})).status).toBe(403);   // not even lower: it is the operator's call
 expect((await call(shop!.environment,'mallory')).status).toBe(403);
 catalog.close();
});

test('the operator sees the allocation, and a raise that would overcommit the gateway is refused',async()=>{
 const {catalog,ready,call}=clients(),[shop]=ready;                          // 4 x 8 = 32 already
 expect((await (await call(shop!.environment,'alice')).json()).data).toMatchObject({operator:true,total:32,allocated:32});
 const over=await call(shop!.environment,'alice','PUT',{share:9});
 expect(over.status).toBe(409);expect((await over.json()).message).toBe('Shares exceed gateway capacity');
 expect((await call(ready[1]!.environment,'alice','PUT',{share:4})).status).toBe(200);
 const raised=await call(shop!.environment,'alice','PUT',{share:12});
 expect(raised.status).toBe(200);expect((await raised.json()).data).toMatchObject({share:12,allocated:32});
 expect(catalog.gatewayShare(shop!.runtime)).toBe(12);
 for(const body of [{share:0},{share:25},{share:1.5},{share:'8'},{share:8,extra:1}])
  expect((await call(shop!.environment,'alice','PUT',body)).status).toBe(400);
 catalog.close();
});

test('the gate guarantees a raised share and lends above it',async()=>{
 const gate=new ConcurrencyGate(2,10,30_000,30_000,{ceiling:6,headroom:2},()=>0);
 gate.useShares(environment=>environment==='shop'?4:undefined);
 const held:(()=>Promise<void>)[]=[];
 const hold=(environment:string)=>{let done:((r:Response)=>void)|undefined;
  const pending=gate.run(environment,new Request('http://localhost/'),()=>new Promise<Response>(resolve=>{done=resolve;}));
  held.push(async()=>{done?.(new Response(null,{status:204}));await pending;});};
 for(let k=0;k<6;k++)hold('shop');await Bun.sleep(0);
 const sample=gate.pressure().get('shop')!;
 expect(sample.guarantee).toBe(4);expect(sample.peak).toBe(6);                   // 4 guaranteed, 2 borrowed
 gate.useShares(()=>{throw new Error('catalog closed');});                       // a failing lookup means the default
 expect(gate.pressure().get('shop')!.guarantee).toBe(2);
 for(const release of held)await release();
});
