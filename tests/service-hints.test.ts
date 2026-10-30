import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {NOTIFY_EVERY_MS,UNUSED_DEFAULT_DAYS,checkUnusedServices,unusedDays,unusedHint} from '../src/control/service-hints';
import {composeServices,installationServicesHandler,servicesHandler} from '../src/control/services';
import {ServiceActivity} from '../src/gateway/activity';
import {unusedText,type Service} from '../ui/services-view';

const DAY=86_400_000,NOW=Date.UTC(2026,9,30,12);
const base={state:'on' as const,inUse:0,lastUsedAt:null,onSince:NOW-11*DAY,now:NOW,days:7};

test('a service on for the whole period with no use gets a hint, counted from the later of its last use and turn on',()=>{
 expect(unusedHint(base)).toEqual({days:11,neverUsed:true});
 expect(unusedHint({...base,lastUsedAt:NOW-8*DAY})).toEqual({days:8,neverUsed:false});
 expect(unusedHint({...base,lastUsedAt:NOW-20*DAY,onSince:NOW-2*DAY})).toBeNull();     // turned on two days ago: not unused for a week
 expect(unusedHint({...base,lastUsedAt:NOW-6*DAY})).toBeNull();
 expect(unusedHint({...base,lastUsedAt:NOW-7*DAY})).toEqual({days:7,neverUsed:false});   // exactly the period
 expect(unusedHint({...base,lastUsedAt:NOW-9*DAY,onSince:NOW-8*DAY})).toEqual({days:8,neverUsed:true});   // used, then turned off and on: unused since it was turned on
 expect(unusedHint({...base,inUse:null})).toEqual({days:11,neverUsed:true});
});

test('nothing is hinted while the service is not plainly on, in use, or sleeping by its own mode',()=>{
 for(const state of ['off','starting','stopping','failed'] as const)expect(unusedHint({...base,state})).toBeNull();
 expect(unusedHint({...base,inUse:1})).toBeNull();
 expect(unusedHint({...base,mode:'on_demand'})).toBeNull();
 expect(unusedHint({...base,onSince:null,lastUsedAt:null})).toBeNull();
});

test('the period has a floor of one day and a ceiling, and a bad value means the default',()=>{
 expect(unusedDays('0')).toBe(1);expect(unusedDays(0)).toBe(1);expect(unusedDays('-3')).toBe(UNUSED_DEFAULT_DAYS);expect(unusedDays('999999')).toBe(UNUSED_DEFAULT_DAYS);
 expect(unusedDays('400')).toBe(365);expect(unusedDays('3')).toBe(3);expect(unusedDays(undefined)).toBe(UNUSED_DEFAULT_DAYS);expect(unusedDays('x')).toBe(UNUSED_DEFAULT_DAYS);
 expect(unusedHint({...base,days:0,onSince:NOW-DAY/2})).toBeNull();           // even a stored 0 cannot report within a day
});

function ready() {
 const catalog=new Catalog(':memory:');
 catalog.initializeInstallation('op-1','alice','Home');
 const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');
 const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'),'production');
 const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
 return {catalog,environment,runtime:job.runtime};
}
const turnOn=(catalog:Catalog,runtime:string,service:'realtime_settings'|'functions_settings',at:number)=>
 catalog.db.query(`INSERT INTO ${service}(runtime,desired,state,failure,actor,updated_at) VALUES (?,'on','on',NULL,'alice',?)`).run(runtime,at);
const notices=(catalog:Catalog)=>catalog.db.query<{kind:string;dedupe_key:string},[]>("SELECT kind,dedupe_key FROM notification_outbox WHERE kind='service.unused'").all();

test('one notice per unused service, repeated at most every 14 days, and the service itself is never changed',()=>{
 const {catalog,runtime}=ready();
 try{
  const activity=new ServiceActivity(undefined,()=>NOW);
  turnOn(catalog,runtime,'realtime_settings',NOW-11*DAY);turnOn(catalog,runtime,'functions_settings',NOW-2*DAY);
  const before=JSON.stringify(catalog.servicesOf(runtime));
  expect(checkUnusedServices(catalog,activity,NOW)).toEqual([{runtime,service:'realtime',days:11}]);
  expect(notices(catalog)).toEqual([{kind:'service.unused',dedupe_key:`service.unused|${runtime}|realtime`}]);
  expect(JSON.stringify(catalog.servicesOf(runtime))).toBe(before);
  expect(checkUnusedServices(catalog,activity,NOW+DAY)).toEqual([]);                           // the next day: nothing new
  // Just under 14 days on, realtime is not reported again; functions has by now sat unused for a week and is reported once.
  expect(checkUnusedServices(catalog,activity,NOW+NOTIFY_EVERY_MS-1)).toEqual([{runtime,service:'functions',days:15}]);
  expect(checkUnusedServices(catalog,activity,NOW+NOTIFY_EVERY_MS)).toEqual([{runtime,service:'realtime',days:25}]);
 }finally{catalog.close();}
});

test('a service that was used, that has a socket open, or that is off is not reported',()=>{
 const {catalog,runtime}=ready();
 try{
  let now=NOW;
  const activity=new ServiceActivity({load:r=>catalog.serviceActivity(r),save:()=>{}},()=>now);
  turnOn(catalog,runtime,'realtime_settings',NOW-30*DAY);
  catalog.saveServiceActivity([{runtime,service:'realtime',at:NOW-DAY}]);
  expect(checkUnusedServices(catalog,activity,NOW)).toEqual([]);
  catalog.db.query("DELETE FROM service_activity").run();
  const close=activity.socketOpened(runtime);now=NOW+40*DAY;                                      // opened long ago, still open, nothing recorded since
  expect(checkUnusedServices(catalog,activity,now)).toEqual([]);
  close();now+=DAY;
  catalog.db.query("UPDATE realtime_settings SET state='off',desired='off'").run();
  expect(checkUnusedServices(catalog,activity,now+30*DAY)).toEqual([]);
  expect(notices(catalog)).toEqual([]);
 }finally{catalog.close();}
});

test('the Services answer carries the hint for every member, and a viewer is told they cannot change it',()=>{
 const {catalog,environment,runtime}=ready();
 try{
  turnOn(catalog,runtime,'realtime_settings',NOW-11*DAY);
  const answer=composeServices({state:catalog.services('carol',environment),now:NOW,unusedDays:7});
  expect(answer.services.find(item=>item.id==='realtime')!.unused).toEqual({days:11,neverUsed:true});
  expect(answer.services.find(item=>item.id==='functions')!.unused).toBeNull();
  expect(answer.canChange).toBe(false);
  expect(composeServices({state:catalog.services('carol',environment),now:NOW}).services[0]!.unused).toBeNull();
 }finally{catalog.close();}
});

test('the unused period is an installation setting only an operator reads or changes, with a floor, and each change is audited',async()=>{
 const {catalog,runtime}=ready();
 try{
  const handler=installationServicesHandler(catalog,async request=>request.headers.get('authorization'));
  const call=(actor:string,method='GET',body?:unknown)=>handler(new Request('http://local/management/v1/installation/services',{method,headers:{authorization:actor,'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
  expect((await call('carol')).status).toBe(403);
  expect(((await (await call('alice')).json()) as {data:{unusedDays:number}}).data.unusedDays).toBe(7);
  expect((await call('alice','PUT',{unusedDays:0})).status).toBe(400);
  expect((await call('alice','PUT',{unusedDays:1.5})).status).toBe(400);
  expect((await call('alice','PUT',{unusedDays:'3'})).status).toBe(400);
  expect((await call('alice','PUT',{unusedDays:3,extra:1})).status).toBe(400);
  expect((await call('carol','PUT',{unusedDays:3})).status).toBe(403);
  expect(()=>catalog.setInstallationServices('carol',{unusedDays:3})).toThrow('Forbidden');
  expect(catalog.installationServices().unusedDays).toBe(7);
  expect((await call('alice','PUT',{unusedDays:3})).status).toBe(200);
  expect(catalog.installationServices().unusedDays).toBe(3);
  const audit=catalog.db.query<{actor:string;detail:string},[]>("SELECT actor,detail FROM audit_events WHERE action='services.settings_changed'").all();
  expect(audit).toHaveLength(1);expect(audit[0]!.actor).toBe('alice');expect(JSON.parse(audit[0]!.detail)).toEqual({setting:'unused_days',from:7,to:3});
  // The Services route uses the stored period.
  turnOn(catalog,runtime,'realtime_settings',Date.now()-4*DAY);
  const environment=catalog.db.query<{environment:string},[string]>('SELECT environment FROM provision_jobs WHERE runtime=?').get(runtime)!.environment;
  const services=servicesHandler(catalog,async request=>request.headers.get('authorization'),{});
  const body=await (await services(new Request(`http://local/management/v1/environments/${environment}/services`,{headers:{authorization:'alice'}}))).json() as {data:{services:{id:string;unused:unknown}[]}};
  expect(body.data.services.find(item=>item.id==='realtime')!.unused).toMatchObject({days:4});
 }finally{catalog.close();}
});

test('the hint reads in words, per service and per case',()=>{
 const service=(over:Partial<Service>):Service=>({id:'realtime',state:'on',failure:null,updatedAt:null,memoryBytes:183*1048576,memoryLimitBytes:320*1048576,costBytes:null,lastUsedAt:null,tracked:true,inUse:0,unused:{days:11,neverUsed:false},...over});
 expect(unusedText(service({}))).toBe('No connection for 11 days, 183 MiB. Turn it off?');
 expect(unusedText(service({id:'functions',unused:{days:1,neverUsed:false}}))).toBe('No call for 1 day, 183 MiB. Turn it off?');
 expect(unusedText(service({unused:{days:9,neverUsed:true},memoryBytes:null}))).toBe('No connection since it was turned on 9 days ago. Turn it off?');
 expect(unusedText(service({unused:null}))).toBeNull();
});
