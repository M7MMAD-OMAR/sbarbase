import {test,expect} from 'bun:test';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {OnDemand} from '../src/control/on-demand';
import {ServiceActivity} from '../src/gateway/activity';
import {ConcurrencyGate} from '../src/gateway/concurrency';
import {managedGateway} from '../src/gateway/managed';
import {realtimeUpgrade} from '../src/gateway/realtime';
import {MAX_HELD_PER_ENVIRONMENT,WAKES_PER_HOUR,type OnDemandService} from '../src/control/on-demand-policy';
import {composeServices} from '../src/control/services';

const MIN=60_000;
function ready(extra=0) {
 const catalog=new Catalog(':memory:');
 catalog.initializeInstallation('op-1','alice','Home');
 // These tests choose each mode themselves; Realtime On demand is allowed here (an installation setting) so its path is exercised too.
 catalog.setInstallationServices('alice',{defaultMode:'off',realtimeOnDemand:true});
 const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'carol','viewer');catalog.setMember('alice',org,'dave','admin');
 const runtimes:{environment:string;runtime:string}[]=[];
 for(let index=0;index<1+extra;index++){
  const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,'P'+index),'production');
  const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
  runtimes.push({environment,runtime:job.runtime});
 }
 return {catalog,org,...runtimes[0]!,all:runtimes};
}
const table=(service:OnDemandService)=>service==='realtime'?'realtime_settings':'functions_settings';
const setState=(catalog:Catalog,runtime:string,service:OnDemandService,desired:'on'|'off',state:string,failure:string|null=null)=>
 catalog.db.query(`INSERT INTO ${table(service)}(runtime,desired,state,failure,actor,updated_at) VALUES (?,?,?,?,'test',1) ON CONFLICT(runtime) DO UPDATE SET desired=excluded.desired,state=excluded.state,failure=excluded.failure`).run(runtime,desired,state,failure);
const row=(catalog:Catalog,runtime:string,service:OnDemandService)=>catalog.db.query<{desired:string;state:string;actor:string;failure:string|null},[string]>(`SELECT desired,state,actor,failure FROM ${table(service)} WHERE runtime=?`).get(runtime);
const events=(catalog:Catalog,action:string)=>catalog.db.query<{actor:string;detail:string},[string]>('SELECT actor,detail FROM audit_events WHERE action=? ORDER BY rowid').all(action).map(item=>({actor:item.actor,...JSON.parse(item.detail)}));

/** A supervisor that does what lab/realtime.py does: applies each pending row when asked to tick, and the service is then running or not. */
function supervisor(catalog:Catalog,running:Set<string>,options:{fail?:string;ticksToStart?:number}={}) {
 let ticks=0;const applied:string[]=[];
 return async()=>{
  for(const [service,table_] of [['realtime','realtime_settings'],['functions','functions_settings']] as const){
   for(const item of catalog.db.query<{runtime:string;desired:string},[]>(`SELECT runtime,desired FROM ${table_} WHERE state='pending'`).all()){
    if(item.desired==='on'&&options.ticksToStart&&++ticks<options.ticksToStart)continue;
    if(item.desired==='on'&&options.fail){catalog.db.query(`UPDATE ${table_} SET state='failed',failure=? WHERE runtime=?`).run(options.fail,item.runtime);continue;}
    catalog.db.query(`UPDATE ${table_} SET state=?,failure=NULL WHERE runtime=?`).run(item.desired,item.runtime);
    applied.push(`${service}:${item.desired}`);
    if(item.desired==='on')running.add(item.runtime+'|'+service);else running.delete(item.runtime+'|'+service);
   }
  }
 };
}

test('choosing a mode: Off and On act now, On demand never starts or stops anything, and every change is audited with its actor',()=>{
 const {catalog,environment,runtime}=ready();
 try{
  expect(catalog.serviceMode(runtime,'realtime')).toEqual({mode:'off',idleMinutes:30,saved:false});
  expect(catalog.serviceMode(runtime,'functions').idleMinutes).toBe(15);
  catalog.setServiceMode('alice',environment,'realtime','on_demand');
  expect(row(catalog,runtime,'realtime')).toBeNull();                                   // nothing was asked of the supervisor
  expect(catalog.serviceMode(runtime,'realtime')).toEqual({mode:'on_demand',idleMinutes:30,saved:true});
  catalog.setServiceMode('dave',environment,'realtime','on_demand',45);
  expect(catalog.serviceMode(runtime,'realtime').idleMinutes).toBe(45);
  catalog.setServiceMode('alice',environment,'realtime','on_demand');                    // omitted: kept
  expect(catalog.serviceMode(runtime,'realtime').idleMinutes).toBe(45);
  catalog.setServiceMode('alice',environment,'realtime','on');
  expect(row(catalog,runtime,'realtime')).toMatchObject({desired:'on',state:'pending',actor:'alice'});
  setState(catalog,runtime,'realtime','on','on');
  catalog.setServiceMode('alice',environment,'realtime','on_demand');
  expect(row(catalog,runtime,'realtime')).toMatchObject({desired:'on',state:'on'});     // a running service keeps running until it is quiet
  catalog.setServiceMode('alice',environment,'realtime','off');
  expect(row(catalog,runtime,'realtime')).toMatchObject({desired:'off',state:'pending'});
  const changes=events(catalog,'realtime.mode_changed');
  expect(changes.map(item=>[item.actor,item.from,item.to,item.reason])).toEqual([['alice','off','on_demand','user'],['dave','on_demand','on_demand','user'],['alice','on_demand','on','user'],['alice','on','on_demand','user'],['alice','on_demand','off','user']]);
 }finally{catalog.close();}
});

test('a viewer or a stranger cannot choose a mode, a change in progress is refused, and the quiet period has a floor',()=>{
 const {catalog,environment,runtime}=ready();
 try{
  expect(()=>catalog.setServiceMode('carol',environment,'realtime','on')).toThrow('Forbidden');
  expect(()=>catalog.setServiceMode('mallory',environment,'functions','on_demand')).toThrow();
  expect(()=>catalog.setServiceMode('alice',environment,'realtime','on_demand',4)).toThrow('Invalid services setting');
  expect(()=>catalog.setServiceMode('alice',environment,'realtime','on_demand',1.5)).toThrow('Invalid services setting');
  expect(()=>catalog.setServiceMode('alice',environment,'realtime','sometimes' as never)).toThrow('Invalid services setting');
  expect(()=>catalog.setServiceMode('alice',environment,'cron' as never,'on')).toThrow('Invalid services setting');
  catalog.setServiceMode('alice',environment,'realtime','on_demand',5);
  expect(catalog.serviceMode(runtime,'realtime').idleMinutes).toBe(5);
  setState(catalog,runtime,'functions','on','pending');
  expect(()=>catalog.setServiceMode('alice',environment,'functions','off')).toThrow('Edge Functions change in progress');
  expect(catalog.serviceMode(runtime,'realtime').mode).toBe('on_demand');
 }finally{catalog.close();}
});

test('the existing on and off switch keeps the saved mode in step, and an on demand choice survives a deploy-style request while it sleeps',()=>{
 const {catalog,environment,runtime}=ready();
 try{
  catalog.requestFunctions('alice',environment,true);
  expect(catalog.serviceMode(runtime,'functions').mode).toBe('on');
  setState(catalog,runtime,'functions','on','on');
  catalog.requestFunctions('alice',environment,false);
  expect(catalog.serviceMode(runtime,'functions').mode).toBe('off');
  setState(catalog,runtime,'functions','off','off');
  catalog.setServiceMode('alice',environment,'functions','on_demand');
  setState(catalog,runtime,'functions','off','off');
  expect(catalog.serviceMode(runtime,'functions').mode).toBe('on_demand');
 }finally{catalog.close();}
});

test('a wake, a sleep and a kill switch wake are requests with a system actor and a reason; none changes the saved mode',()=>{
 const {catalog,environment,runtime}=ready();
 try{
  catalog.setServiceMode('alice',environment,'realtime','on_demand');
  expect(catalog.requestServiceChange(runtime,'realtime',true,'system:wake','wake')).toBe('requested');
  expect(row(catalog,runtime,'realtime')).toMatchObject({desired:'on',state:'pending',actor:'system:wake'});
  expect(catalog.requestServiceChange(runtime,'realtime',true,'system:wake','wake')).toBe('pending');     // one change at a time
  setState(catalog,runtime,'realtime','on','on');
  expect(catalog.requestServiceChange(runtime,'realtime',true,'system:wake','wake')).toBe('noop');
  expect(catalog.requestServiceChange(runtime,'realtime',true,'system:wake','wake',true)).toBe('requested');   // the catalog says on but nothing runs
  setState(catalog,runtime,'realtime','on','on');
  expect(catalog.requestServiceChange(runtime,'realtime',false,'system:idle','idle')).toBe('requested');
  expect(row(catalog,runtime,'realtime')).toMatchObject({desired:'off',state:'pending',actor:'system:idle'});
  setState(catalog,runtime,'realtime','off','off');
  expect(catalog.requestServiceChange(runtime,'realtime',false,'system:idle','idle')).toBe('noop');
  expect(catalog.requestServiceChange(runtime,'realtime',true,'system:kill-switch','kill_switch')).toBe('requested');
  expect(catalog.serviceMode(runtime,'realtime').mode).toBe('on_demand');
  expect(events(catalog,'realtime.wake_requested').map(item=>[item.actor,item.reason])).toEqual([['system:wake','wake'],['system:wake','wake'],['system:kill-switch','kill_switch']]);
  expect(events(catalog,'realtime.sleep_requested').map(item=>[item.actor,item.reason])).toEqual([['system:idle','idle']]);
 }finally{catalog.close();}
});

test('a new environment gets Edge Functions On demand by default and Realtime Off; an environment that exists keeps what it has',()=>{
 const catalog=new Catalog(':memory:');
 try{
  catalog.initializeInstallation('op-1','alice','Home');
  const org=catalog.createOrganization('alice','A');
  const make=(name:string,before?:(runtime:string)=>void)=>{
   const environment=catalog.createEnvironment('alice',catalog.createProject('alice',org,name),'production');
   const job=catalog.claimProvision()!;before?.(job.runtime);catalog.finishProvision(environment,job.claim!,true);return {environment,runtime:job.runtime};
  };
  expect(catalog.installationServices()).toMatchObject({defaultMode:'on_demand',realtimeOnDemand:false});
  const fresh=make('P1');
  expect(catalog.serviceMode(fresh.runtime,'functions')).toEqual({mode:'on_demand',idleMinutes:15,saved:true});
  expect(catalog.serviceMode(fresh.runtime,'realtime')).toEqual({mode:'off',idleMinutes:30,saved:false});     // too slow to be a default: see on-demand-policy.ts
  expect(row(catalog,fresh.runtime,'functions')).toBeNull();                                                    // a default never starts anything
  expect(()=>catalog.setServiceMode('alice',fresh.environment,'realtime','on_demand')).toThrow('Invalid services setting');
  catalog.setInstallationServices('alice',{realtimeOnDemand:true,idleMinutes:{realtime:40,functions:20}});
  expect(()=>catalog.setServiceMode('alice',fresh.environment,'realtime','on_demand')).not.toThrow();
  expect(catalog.serviceMode(fresh.runtime,'functions').idleMinutes).toBe(15);                                  // what exists is untouched
  const second=make('P2');
  expect(catalog.serviceMode(second.runtime,'realtime')).toEqual({mode:'on_demand',idleMinutes:40,saved:true});
  expect(catalog.serviceMode(second.runtime,'functions')).toEqual({mode:'on_demand',idleMinutes:20,saved:true});
  // An environment that already asked for a service before it finished keeps its choice.
  const asked=make('P3',runtime=>setState(catalog,runtime,'functions','on','on'));
  expect(catalog.serviceMode(asked.runtime,'functions')).toMatchObject({mode:'on',saved:false});
  expect(events(catalog,'functions.mode_changed').filter(item=>item.reason==='default').map(item=>item.actor)).toEqual(['system:default','system:default']);
  // Off for new environments is a choice an operator can make.
  catalog.setInstallationServices('alice',{defaultMode:'off'});
  const quiet=make('P4');
  expect(catalog.serviceMode(quiet.runtime,'functions').saved).toBe(false);
 }finally{catalog.close();}
});

test('the installation settings are validated, audited, and show the read only limits',()=>{
 const {catalog}=ready();
 try{
  expect(catalog.installationServices()).toMatchObject({killSwitch:false,defaultMode:'off',idleMinutes:{realtime:30,functions:15},limits:{wakesPerHour:WAKES_PER_HOUR,holdSeconds:20}});
  expect(()=>catalog.setInstallationServices('alice',{realtimeOnDemand:'yes' as never})).toThrow('Invalid services setting');
  expect(()=>catalog.setInstallationServices('carol',{killSwitch:true})).toThrow('Forbidden');
  for(const bad of [{idleMinutes:{realtime:4}},{idleMinutes:{functions:2.5}},{defaultMode:'always'},{killSwitch:'yes' as never}])
   expect(()=>catalog.setInstallationServices('alice',bad)).toThrow('Invalid services setting');
  catalog.setInstallationServices('alice',{killSwitch:true,idleMinutes:{functions:60}});
  expect(catalog.installationServices()).toMatchObject({killSwitch:true,idleMinutes:{realtime:30,functions:60}});
  catalog.setInstallationServices('alice',{killSwitch:true});                                  // no change, no event
  const audit=catalog.db.query<{detail:string},[]>("SELECT detail FROM audit_events WHERE action='services.settings_changed' AND detail NOT LIKE '%default_mode%' AND detail NOT LIKE '%realtime_on_demand%' ORDER BY rowid").all().map(item=>JSON.parse(item.detail));
  expect(audit).toEqual([{setting:'kill_switch',from:false,to:true},{setting:'idle_functions',from:15,to:60}]);
 }finally{catalog.close();}
});

function harness(options:{ticksToStart?:number;fail?:string;holdMs?:number;now?:()=>number;extra?:number}={}) {
 const base=ready(options.extra??0);
 const running=new Set<string>();
 const tick=supervisor(base.catalog,running,options);
 const activity=new ServiceActivity(undefined,options.now??Date.now);
 const onDemand=new OnDemand(base.catalog,activity,(runtime,service)=>running.has(runtime+'|'+service),{holdMs:options.holdMs??5000,pause:async()=>{await tick();await Promise.resolve();},pollMs:1,now:options.now});
 return {...base,running,tick,activity,onDemand};
}

test('a service saved as Off or On is never woken by a request, and one that is up answers at once',async()=>{
 const h=harness();
 try{
  expect(await h.onDemand.ensure(h.runtime,'functions')).toMatchObject({ok:false,status:404});
  h.catalog.setServiceMode('alice',h.environment,'functions','on');
  h.catalog.db.query("UPDATE functions_settings SET state='off',desired='off'").run();
  expect(await h.onDemand.ensure(h.runtime,'functions')).toMatchObject({ok:false,status:404});
  expect(row(h.catalog,h.runtime,'functions')).toMatchObject({desired:'off',state:'off'});
  h.catalog.setServiceMode('alice',h.environment,'functions','on_demand');
  setState(h.catalog,h.runtime,'functions','on','on');h.running.add(h.runtime+'|functions');
  expect(await h.onDemand.ensure(h.runtime,'functions')).toEqual({ok:true});
  expect(events(h.catalog,'functions.wake_requested')).toEqual([]);
 }finally{h.catalog.close();}
});

test('the first request wakes a sleeping service and waits for it; many requests share one wake',async()=>{
 const h=harness({ticksToStart:3});
 try{
  h.catalog.setServiceMode('alice',h.environment,'realtime','on_demand');
  const results=await Promise.all([1,2,3,4,5].map(()=>h.onDemand.ensure(h.runtime,'realtime')));
  expect(results).toEqual([{ok:true},{ok:true},{ok:true},{ok:true},{ok:true}]);
  expect(events(h.catalog,'realtime.wake_requested')).toHaveLength(1);
  expect(h.running.has(h.runtime+'|realtime')).toBe(true);
  expect(h.catalog.serviceMode(h.runtime,'realtime').mode).toBe('on_demand');
 }finally{h.catalog.close();}
});

test('a start slower than the hold answers 503 with retry-after, never a hang, and the next request succeeds',async()=>{
 let now=0;
 const h=harness({ticksToStart:1_000_000,holdMs:50,now:()=>now});
 try{
  h.catalog.setServiceMode('alice',h.environment,'functions','on_demand');
  const slow=new OnDemand(h.catalog,h.activity,(runtime,service)=>h.running.has(runtime+'|'+service),{holdMs:50,pollMs:1,pause:async()=>{now+=10;await Promise.resolve();},now:()=>now});
  const outcome=await slow.ensure(h.runtime,'functions');
  expect(outcome).toMatchObject({ok:false,status:503});
  expect(outcome.ok?0:outcome.retryAfter).toBeGreaterThan(0);
  expect(row(h.catalog,h.runtime,'functions')).toMatchObject({desired:'on',state:'pending'});   // the start goes on; the client retries
  setState(h.catalog,h.runtime,'functions','on','on');h.running.add(h.runtime+'|functions');
  expect(await slow.ensure(h.runtime,'functions')).toEqual({ok:true});
  expect(events(h.catalog,'functions.wake_requested')).toHaveLength(1);                          // the retry did not ask again
 }finally{h.catalog.close();}
});

test('a refusal for lack of room says so in words, with a retry after, and the next request tries again',async()=>{
 const h=harness({fail:'This server does not have the room, in memory or database connections, to run Realtime for another environment.'});
 try{
  h.catalog.setServiceMode('alice',h.environment,'realtime','on_demand');
  const outcome=await h.onDemand.ensure(h.runtime,'realtime');
  expect(outcome).toMatchObject({ok:false,status:503});
  expect(outcome.ok?'':outcome.message).toContain('Could not wake');
  expect(outcome.ok?'':outcome.message).toContain('does not have the room');
  expect(outcome.ok?'':outcome.message).toContain('keeps working without it');
  expect(row(h.catalog,h.runtime,'realtime')).toMatchObject({state:'failed'});
  expect((await h.onDemand.ensure(h.runtime,'realtime')).ok).toBe(false);
  expect(events(h.catalog,'realtime.wake_requested')).toHaveLength(2);
  const view=composeServices({state:h.catalog.servicesOf(h.runtime),now:Date.now()});
  expect(view.services.find(item=>item.id==='realtime')).toMatchObject({state:'failed',mode:'on_demand'});
 }finally{h.catalog.close();}
});

test('at most six wakes an hour per service and environment, then a 429; the kill switch and other environments do not count',async()=>{
 let now=1_000_000;
 const h=harness({now:()=>now,extra:1});
 try{
  h.catalog.setServiceMode('alice',h.environment,'functions','on_demand');
  for(let index=0;index<WAKES_PER_HOUR;index++){
   expect(await h.onDemand.ensure(h.runtime,'functions')).toEqual({ok:true});
   h.catalog.db.query("UPDATE functions_settings SET state='off',desired='off' WHERE runtime=?").run(h.runtime);h.running.delete(h.runtime+'|functions');
  }
  // The audit times are real time; align the window with the fake clock.
  h.catalog.db.query("UPDATE audit_events SET at=?").run(now);
  expect(await h.onDemand.ensure(h.runtime,'functions')).toMatchObject({ok:false,status:429});
  expect(events(h.catalog,'functions.wake_requested')).toHaveLength(WAKES_PER_HOUR);
  now+=3_600_001;
  expect(await h.onDemand.ensure(h.runtime,'functions')).toEqual({ok:true});
  // Another environment has its own count.
  const other=h.all[1]!;
  h.catalog.setServiceMode('alice',other.environment,'functions','on_demand');
  expect(await h.onDemand.ensure(other.runtime,'functions')).toEqual({ok:true});
 }finally{h.catalog.close();}
});

test('one wake at a time on the installation: a second service waits for the first to finish',async()=>{
 const h=harness({ticksToStart:2,extra:1});
 try{
  const other=h.all[1]!;
  h.catalog.setServiceMode('alice',h.environment,'realtime','on_demand');
  h.catalog.setServiceMode('alice',other.environment,'functions','on_demand');
  const order:string[]=[];
  const first=h.onDemand.ensure(h.runtime,'realtime').then(result=>{order.push('first done');return result;});
  const second=h.onDemand.ensure(other.runtime,'functions').then(result=>{order.push('second done');return result;});
  await Promise.resolve();
  // While the first is starting, the second has asked for nothing.
  expect(row(h.catalog,other.runtime,'functions')).toBeNull();
  expect(await Promise.all([first,second])).toEqual([{ok:true},{ok:true}]);
  expect(order).toEqual(['first done','second done']);
  const times=h.catalog.db.query<{action:string},[]>("SELECT action FROM audit_events WHERE action LIKE '%.wake_requested' ORDER BY rowid").all().map(item=>item.action);
  expect(times).toEqual(['realtime.wake_requested','functions.wake_requested']);
 }finally{h.catalog.close();}
});

test('a request that arrives before the supervisor has applied the sleeper\'s stop calls the stop off and the service keeps running',async()=>{
 const h=harness();
 try{
  h.catalog.setServiceMode('alice',h.environment,'realtime','on_demand');
  setState(h.catalog,h.runtime,'realtime','on','on');h.running.add(h.runtime+'|realtime');
  expect(h.catalog.requestServiceChange(h.runtime,'realtime',false,'system:idle','idle')).toBe('requested');
  expect(await h.onDemand.ensure(h.runtime,'realtime')).toEqual({ok:true});
  expect(row(h.catalog,h.runtime,'realtime')).toMatchObject({desired:'on',state:'on'});
  expect(events(h.catalog,'realtime.sleep_cancelled')).toHaveLength(1);
  expect(events(h.catalog,'realtime.wake_requested')).toHaveLength(0);          // no stop, no start, no wake spent from the hour's budget
  expect(h.running.has(h.runtime+'|realtime')).toBe(true);
 }finally{h.catalog.close();}
});

test('a request that arrives while the container is already being stopped waits for the stop, then wakes it',async()=>{
 const h=harness();
 try{
  h.catalog.setServiceMode('alice',h.environment,'realtime','on_demand');
  setState(h.catalog,h.runtime,'realtime','on','on');
  expect(h.catalog.requestServiceChange(h.runtime,'realtime',false,'system:idle','idle')).toBe('requested');
  // The container is already gone (the supervisor is mid stop): nothing to cancel.
  expect(await h.onDemand.ensure(h.runtime,'realtime')).toEqual({ok:true});
  expect(events(h.catalog,'realtime.sleep_cancelled')).toHaveLength(0);
  expect(events(h.catalog,'realtime.wake_requested')).toHaveLength(1);
 }finally{h.catalog.close();}
});

test('a sleeper request that a person\'s own change replaced is not called off by a wake',()=>{
 const h=harness();
 try{
  h.catalog.setServiceMode('alice',h.environment,'realtime','on_demand');
  setState(h.catalog,h.runtime,'realtime','on','on');h.running.add(h.runtime+'|realtime');
  h.catalog.setServiceMode('alice',h.environment,'realtime','off');                 // a person asked for the stop
  expect(h.catalog.cancelSleep(h.runtime,'realtime')).toBe(false);
  expect(row(h.catalog,h.runtime,'realtime')).toMatchObject({desired:'off',state:'pending',actor:'alice'});
 }finally{h.catalog.close();}
});

test('an On demand service that was just woken is not put to sleep before the request that woke it is served, and broadcasts and public calls keep it up',async()=>{
 let now=10*60*MIN;
 const h=harness({now:()=>now});
 try{
  h.catalog.setServiceMode('alice',h.environment,'functions','on_demand',5);
  now+=600*MIN;                                                                         // a long quiet time: the last use is hours old
  expect(await h.onDemand.ensure(h.runtime,'functions')).toEqual({ok:true});            // woken by a request
  now+=61_000;
  expect(h.onDemand.sweep(now)).toEqual({slept:0,woke:0});                              // the sweeper looks a minute later: it is in use
  expect(h.activity.lastUsed(h.runtime,'functions')).toBe(now-61_000);
  now+=6*MIN;
  expect(h.onDemand.sweep(now)).toEqual({slept:1,woke:0});                              // and sleeps once it has been quiet its whole period
 }finally{h.catalog.close();}
});

test('the gateway counts a verified Realtime broadcast and a call to a public function as use, and not an anonymous call to a private one',async()=>{
 const g=gatewayWith({publicFunctions:['webhook']});
 try{
  // Saved as On (not On demand): only the gateway's own count marks the use here.
  g.catalog.setServiceMode('alice',g.environment,'functions','on');g.catalog.setServiceMode('alice',g.environment,'realtime','on');
  setState(g.catalog,g.runtime,'functions','on','on');g.running.add(g.runtime+'|functions');
  setState(g.catalog,g.runtime,'realtime','on','on');g.running.add(g.runtime+'|realtime');
  await g.gateway(new Request(`http://local/${g.runtime}/functions/v1/private-thing`,{method:'POST',body:'{}'}));
  expect(g.activity.lastUsed(g.runtime,'functions')).toBeNull();
  await (await g.gateway(new Request(`http://local/${g.runtime}/functions/v1/webhook`,{method:'POST',body:'{}'}))).text();
  expect(g.activity.lastUsed(g.runtime,'functions')).not.toBeNull();
  expect(g.activity.lastUsed(g.runtime,'realtime')).toBeNull();
  await g.gateway(new Request(`http://local/${g.runtime}/realtime/v1/api/broadcast`,{method:'POST',headers:{apikey:'wrong'},body:'{}'}));
  expect(g.activity.lastUsed(g.runtime,'realtime')).toBeNull();
  const key=g.keys.issue(g.runtime).token;
  await (await g.gateway(new Request(`http://local/${g.runtime}/realtime/v1/api/broadcast`,{method:'POST',headers:{apikey:key},body:'{}'}))).text();
  expect(g.activity.lastUsed(g.runtime,'realtime')).not.toBeNull();
 }finally{g.close();}
});

test('a kill switch does not try a failed start again every sweep, only after ten minutes',()=>{
 let now=10*60*MIN;
 const h=harness({now:()=>now});
 try{
  h.catalog.setServiceMode('alice',h.environment,'functions','on_demand');
  h.catalog.setInstallationServices('alice',{killSwitch:true});
  expect(h.onDemand.sweep(now)).toEqual({slept:0,woke:1});
  h.catalog.db.query("UPDATE functions_settings SET state='failed',failure='no room',updated_at=?").run(now);
  now+=10_000;expect(h.onDemand.sweep(now)).toEqual({slept:0,woke:0});
  now+=9*MIN;expect(h.onDemand.sweep(now)).toEqual({slept:0,woke:0});
  now+=2*MIN;expect(h.onDemand.sweep(now)).toEqual({slept:0,woke:1});
  expect(events(h.catalog,'functions.wake_requested')).toHaveLength(2);
 }finally{h.catalog.close();}
});

test('the mode is read from the catalog at most once every two seconds per service, and a change shows within that',()=>{
 let now=1_000;
 const h=harness({now:()=>now});
 try{
  let reads=0;const original=h.catalog.serviceMode.bind(h.catalog);
  h.catalog.serviceMode=(runtime,service)=>{reads++;return original(runtime,service);};
  h.catalog.setServiceMode('alice',h.environment,'functions','on_demand');reads=0;
  expect(h.onDemand.isOnDemand(h.runtime,'functions')).toBe(true);
  for(let index=0;index<50;index++)h.onDemand.isOnDemand(h.runtime,'functions');
  expect(reads).toBe(1);
  h.catalog.setServiceMode('alice',h.environment,'functions','off');
  now+=2_001;
  expect(h.onDemand.isOnDemand(h.runtime,'functions')).toBe(false);
 }finally{h.catalog.close();}
});

test('a request for a public function is recognised from the manifest, and the answer is kept for two seconds',async()=>{
 const {isPublicFunction}=await import('../src/control/functions');
 const {mkdtempSync,mkdirSync,writeFileSync,rmSync}=await import('node:fs');
 const {tmpdir}=await import('node:os');const {join}=await import('node:path');
 const root=mkdtempSync(join(tmpdir(),'public-'));
 const R='e_'+'f'.repeat(24);
 try{
  mkdirSync(join(root,R),{recursive:true});
  const write=(verify:boolean)=>writeFileSync(join(root,R,'functions.json'),JSON.stringify({functions:{hook:{version:'v1',verify_jwt:verify,updated_at:1,size:1}}}));
  write(false);
  expect(isPublicFunction(root,R,'hook',1_000_000)).toBe(true);
  expect(isPublicFunction(root,R,'other',1_000_000)).toBe(false);
  expect(isPublicFunction(root,R,'constructor',1_000_000)).toBe(false);
  write(true);
  expect(isPublicFunction(root,R,'hook',1_001_000)).toBe(true);                         // still the cached answer
  expect(isPublicFunction(root,R,'hook',1_002_500)).toBe(false);                        // read again
  expect(isPublicFunction(root,'e_'+'0'.repeat(24),'hook',1_010_000)).toBe(false);      // no manifest: not public
 }finally{rmSync(root,{recursive:true,force:true});}
});

test('a catalog that says on while nothing runs is woken again, not trusted',async()=>{
 const h=harness();
 try{
  h.catalog.setServiceMode('alice',h.environment,'functions','on_demand');
  setState(h.catalog,h.runtime,'functions','on','on');            // but the endpoint is not published
  expect(await h.onDemand.ensure(h.runtime,'functions')).toEqual({ok:true});
  expect(events(h.catalog,'functions.wake_requested')).toHaveLength(1);
 }finally{h.catalog.close();}
});

test('requests held for one environment are capped, so a flood cannot pile up memory',async()=>{
 const h=harness({ticksToStart:1_000_000,holdMs:30});
 try{
  h.catalog.setServiceMode('alice',h.environment,'realtime','on_demand');
  const small=new OnDemand(h.catalog,h.activity,()=>false,{holdMs:200,maxHeld:3,pollMs:5});
  const results=await Promise.all(Array.from({length:6},()=>small.ensure(h.runtime,'realtime')));
  const refused=results.filter(item=>!item.ok&&item.message.includes('Too many'));
  expect(refused).toHaveLength(3);
  expect(MAX_HELD_PER_ENVIRONMENT).toBeGreaterThan(3);
 }finally{h.catalog.close();}
});

test('the sleeper puts a quiet On demand service to sleep only after its whole quiet period, and never one that is in use',()=>{
 let now=10*60*MIN;
 const h=harness({now:()=>now});
 try{
  h.catalog.setServiceMode('alice',h.environment,'realtime','on_demand',5);
  h.catalog.setServiceMode('alice',h.environment,'functions','on_demand',5);
  for(const service of ['realtime','functions'] as const){setState(h.catalog,h.runtime,service,'on','on');h.running.add(h.runtime+'|'+service);}
  const start=now;
  // This process has only just begun: nothing is judged idle yet, whatever the catalog says.
  expect(h.onDemand.sweep(now)).toEqual({slept:0,woke:0});
  now=start+4*MIN;h.activity.touch(h.runtime,'functions');
  now=start+6*MIN;
  const close=h.activity.socketOpened(h.runtime);                                   // a socket is open: Realtime stays
  now=start+30*MIN;
  expect(h.onDemand.sweep(now)).toEqual({slept:1,woke:0});                           // functions was last used 26 minutes ago
  expect(row(h.catalog,h.runtime,'functions')).toMatchObject({desired:'off',state:'pending',actor:'system:idle'});
  expect(row(h.catalog,h.runtime,'realtime')).toMatchObject({desired:'on',state:'on'});
  close();
  now=start+31*MIN;
  expect(h.onDemand.sweep(now)).toEqual({slept:0,woke:0});                           // sleep is judged once a minute, and the close is a use
  now=start+40*MIN;
  expect(h.onDemand.sweep(now)).toEqual({slept:1,woke:0});
  expect(events(h.catalog,'realtime.sleep_requested')[0]).toMatchObject({actor:'system:idle',reason:'idle'});
 }finally{h.catalog.close();}
});

test('the sleeper never touches a service saved as On or Off, one with a change in progress, or an Edge Functions call in flight',()=>{
 let now=10*60*MIN,inFlight=1;
 const base=ready();
 try{
  const running=new Set<string>();
  const activity=new ServiceActivity(undefined,()=>now,()=>inFlight);
  const onDemand=new OnDemand(base.catalog,activity,(runtime,service)=>running.has(runtime+'|'+service));
  const {catalog,environment,runtime}=base;
  setState(catalog,runtime,'realtime','on','on');running.add(runtime+'|realtime');
  catalog.setServiceMode('alice',environment,'realtime','on');
  now+=600*MIN;
  expect(onDemand.sweep(now)).toEqual({slept:0,woke:0});
  catalog.setServiceMode('alice',environment,'functions','on_demand');
  setState(catalog,runtime,'functions','on','on');running.add(runtime+'|functions');
  now+=600*MIN;
  expect(onDemand.sweep(now)).toEqual({slept:0,woke:0});                             // a call is in flight
  inFlight=0;setState(catalog,runtime,'functions','on','pending');
  now+=600*MIN;
  expect(onDemand.sweep(now)).toEqual({slept:0,woke:0});                             // a change is already running
  setState(catalog,runtime,'functions','on','on');
  now+=600*MIN;
  expect(onDemand.sweep(now)).toEqual({slept:1,woke:0});
 }finally{base.catalog.close();}
});

test('the kill switch wakes what sleeps, one at a time, puts nothing to sleep, and keeps every saved choice',()=>{
 let now=10*60*MIN;
 const h=harness({now:()=>now,extra:1});
 try{
  const targets=[{...h.all[0]!,service:'realtime' as const},{...h.all[1]!,service:'functions' as const}];
  for(const target of targets)h.catalog.setServiceMode('alice',target.environment,target.service,'on_demand',5);
  h.catalog.setInstallationServices('alice',{killSwitch:true});
  expect(h.onDemand.sweep(now)).toEqual({slept:0,woke:1});
  const woken=targets.filter(target=>row(h.catalog,target.runtime,target.service)!==null);
  expect(woken).toHaveLength(1);                                                         // the other waits for this one
  expect(row(h.catalog,woken[0]!.runtime,woken[0]!.service)).toMatchObject({desired:'on',state:'pending',actor:'system:kill-switch'});
  expect(h.onDemand.sweep(now)).toEqual({slept:0,woke:0});
  setState(h.catalog,woken[0]!.runtime,woken[0]!.service,'on','on');h.running.add(woken[0]!.runtime+'|'+woken[0]!.service);
  expect(h.onDemand.sweep(now)).toEqual({slept:0,woke:1});
  const second=targets.find(target=>target!==woken[0])!;
  setState(h.catalog,second.runtime,second.service,'on','on');h.running.add(second.runtime+'|'+second.service);
  now+=600*MIN;
  expect(h.onDemand.sweep(now)).toEqual({slept:0,woke:0});                               // quiet for ten hours, still never put to sleep
  for(const target of targets)expect(h.catalog.serviceMode(target.runtime,target.service)).toMatchObject({mode:'on_demand',idleMinutes:5});
  expect(events(h.catalog,`${woken[0]!.service}.wake_requested`)[0]).toMatchObject({actor:'system:kill-switch',reason:'kill_switch'});
  // Switched off again: the services go back to sleeping on their own after their quiet period.
  h.catalog.setInstallationServices('alice',{killSwitch:false});
  now+=10*MIN;
  expect(h.onDemand.sweep(now).slept).toBe(2);
 }finally{h.catalog.close();}
});

test('the Services answer says sleeping and waking in words, and shows the mode, the quiet period and the read only limits',()=>{
 const {catalog,environment,runtime}=ready();
 try{
  catalog.setServiceMode('alice',environment,'realtime','on_demand',20);
  let answer=composeServices({state:catalog.servicesOf(runtime),now:Date.now(),unusedDays:7});
  expect(answer.services.find(item=>item.id==='realtime')).toMatchObject({state:'sleeping',mode:'on_demand',idleMinutes:20,onDemandAvailable:true});
  expect(answer.services.find(item=>item.id==='functions')).toMatchObject({state:'off',mode:'off'});
  expect(answer.limits).toEqual({wakesPerHour:WAKES_PER_HOUR,holdSeconds:20,idleFloorMinutes:5});
  catalog.requestServiceChange(runtime,'realtime',true,'system:wake','wake');
  answer=composeServices({state:catalog.servicesOf(runtime),now:Date.now()});
  expect(answer.services.find(item=>item.id==='realtime')!.state).toBe('waking');
  setState(catalog,runtime,'realtime','on','on');
  expect(composeServices({state:catalog.servicesOf(runtime),now:Date.now()}).services.find(item=>item.id==='realtime')!.state).toBe('on');
  // An On demand service is never reported as unused: it sleeps by itself.
  catalog.db.query("UPDATE realtime_settings SET updated_at=1").run();
  expect(composeServices({state:catalog.servicesOf(runtime),now:Date.now()+30*86_400_000,unusedDays:7}).services.find(item=>item.id==='realtime')!.unused).toBeNull();
  expect(composeServices({state:{...catalog.servicesOf(runtime),killSwitch:true},now:Date.now()}).killSwitch).toBe(true);
 }finally{catalog.close();}
});

// ---- the gateway ----

function gatewayWith(options:{publicFunctions?:string[];holdMs?:number;ticksToStart?:number;fail?:string}={}) {
 const h=harness({holdMs:options.holdMs,ticksToStart:options.ticksToStart,fail:options.fail});
 const keys=new KeyStore(':memory:');
 const calls:string[]=[];
 const transport:typeof fetch=Object.assign(async(url:URL|RequestInfo)=>{calls.push(String(url));return new Response('answered');},{preconnect:()=>{}});
 const resolve=()=>({auth:'http://auth.invalid',rest:'http://rest.invalid',keys:[],anonymousToken:'anon',serviceToken:'service',enabled:true,
  ...(h.running.has(h.runtime+'|functions')?{functions:{url:'http://functions.invalid'}}:{}),
  ...(h.running.has(h.runtime+'|realtime')?{realtime:{url:'http://realtime.invalid:4000',tenantHost:'a'.repeat(24)+'.realtime'},realtimeToken:'rt'}:{})});
 const gate={isOnDemand:(runtime:string,service:OnDemandService)=>h.onDemand.isOnDemand(runtime,service),ensure:(runtime:string,service:OnDemandService)=>h.onDemand.ensure(runtime,service),
  publicFunction:(_runtime:string,name:string)=>(options.publicFunctions??[]).includes(name)};
 const gateway=managedGateway(h.catalog,keys,resolve as never,transport,new ConcurrencyGate(),h.activity,gate);
 return {...h,keys,calls,gateway,resolve,gate,close:()=>{keys.close();h.catalog.close();}};
}

test('the gateway wakes a sleeping Functions only for a verified key, and serves the call once it is up',async()=>{
 const g=gatewayWith({ticksToStart:2});
 try{
  g.catalog.setServiceMode('alice',g.environment,'functions','on_demand');
  const url=`http://local/${g.runtime}/functions/v1/hello`;
  const bad=await g.gateway(new Request(url,{headers:{apikey:'wrong'}}));
  expect(bad.status).toBe(401);
  expect((await g.gateway(new Request(url,{method:'OPTIONS'}))).status).toBe(204);
  expect(row(g.catalog,g.runtime,'functions')).toBeNull();                             // nothing woke
  const key=g.keys.issue(g.runtime).token;
  const answer=await g.gateway(new Request(url,{headers:{apikey:key}}));
  expect(await answer.text()).toBe('answered');
  expect(g.calls).toEqual(['http://functions.invalid/hello']);
  expect(events(g.catalog,'functions.wake_requested')).toHaveLength(1);
 }finally{g.close();}
});

test('a Functions call without a key wakes the runtime only for a function its owner made public',async()=>{
 const g=gatewayWith({publicFunctions:['webhook']});
 try{
  g.catalog.setServiceMode('alice',g.environment,'functions','on_demand');
  const secret=await g.gateway(new Request(`http://local/${g.runtime}/functions/v1/private-thing`,{method:'POST',body:'{}'}));
  expect(secret.status).toBe(404);
  expect(events(g.catalog,'functions.wake_requested')).toHaveLength(0);
  const hook=await g.gateway(new Request(`http://local/${g.runtime}/functions/v1/webhook`,{method:'POST',body:'{}'}));
  expect(await hook.text()).toBe('answered');
  expect(events(g.catalog,'functions.wake_requested')).toHaveLength(1);
 }finally{g.close();}
});

test('a first request that outlasts the hold gets 503 with retry-after, and the call is not forwarded to nothing',async()=>{
 const g=gatewayWith({holdMs:30,ticksToStart:1_000_000});
 try{
  g.catalog.setServiceMode('alice',g.environment,'functions','on_demand');
  const key=g.keys.issue(g.runtime).token;
  const response=await g.gateway(new Request(`http://local/${g.runtime}/functions/v1/hello`,{headers:{apikey:key}}));
  expect(response.status).toBe(503);
  expect(Number(response.headers.get('retry-after'))).toBeGreaterThan(0);
  expect(g.calls).toEqual([]);
 }finally{g.close();}
});

test('a wake that the server cannot hold answers 503 with the reason, and a service saved as Off still answers 404 with no wake',async()=>{
 const g=gatewayWith({fail:'This server does not have the room, in memory or database connections, to run Edge Functions for another environment.'});
 try{
  g.catalog.setServiceMode('alice',g.environment,'functions','on_demand');
  const key=g.keys.issue(g.runtime).token;
  const response=await g.gateway(new Request(`http://local/${g.runtime}/functions/v1/hello`,{headers:{apikey:key}}));
  expect(response.status).toBe(503);
  expect(((await response.json()) as {message:string}).message).toContain('does not have the room');
  g.catalog.setServiceMode('alice',g.environment,'functions','off');
  g.catalog.db.query("UPDATE functions_settings SET state='off',desired='off'").run();
  const before=events(g.catalog,'functions.wake_requested').length;
  const off=await g.gateway(new Request(`http://local/${g.runtime}/functions/v1/hello`,{headers:{apikey:key}}));
  expect(off.status).toBe(404);
  expect(events(g.catalog,'functions.wake_requested')).toHaveLength(before);
 }finally{g.close();}
});

test('the Realtime broadcast API wakes a sleeping Realtime for a verified key only',async()=>{
 const g=gatewayWith();
 try{
  g.catalog.setServiceMode('alice',g.environment,'realtime','on_demand');
  const url=`http://local/${g.runtime}/realtime/v1/api/broadcast`;
  expect((await g.gateway(new Request(url,{method:'POST',headers:{apikey:'wrong'},body:'{}'}))).status).toBe(401);
  expect(row(g.catalog,g.runtime,'realtime')).toBeNull();
  const key=g.keys.issue(g.runtime).token;
  expect((await g.gateway(new Request(url,{method:'POST',headers:{apikey:key},body:'{}'}))).status).toBe(200);
  expect(events(g.catalog,'realtime.wake_requested')).toHaveLength(1);
 }finally{g.close();}
});

test('a Realtime socket waits for the wake only after its key is verified, and is refused with the reason when it cannot start',async()=>{
 const g=gatewayWith();
 try{
  g.catalog.setServiceMode('alice',g.environment,'realtime','on_demand');
  const key=g.keys.issue(g.runtime).token;
  const decide=realtimeUpgrade({route:runtime=>runtime===g.runtime?(g.resolve() as never):undefined,verifyKey:(runtime,candidate)=>g.keys.resolve(runtime,candidate),
   wake:{isOnDemand:runtime=>g.onDemand.isOnDemand(runtime,'realtime'),ensure:runtime=>g.onDemand.ensure(runtime,'realtime')}});
  const headers=new Headers({upgrade:'websocket',connection:'Upgrade','sec-websocket-key':'abc==','sec-websocket-version':'13'});
  const wrong=await decide(`/${g.runtime}/realtime/v1/websocket?apikey=nope&vsn=1`,headers);
  expect(wrong).toMatchObject({ok:false,status:401});
  expect(row(g.catalog,g.runtime,'realtime')).toBeNull();
  const open=await decide(`/${g.runtime}/realtime/v1/websocket?apikey=${key}&vsn=1`,headers);
  expect(open.ok).toBe(true);
  expect(events(g.catalog,'realtime.wake_requested')).toHaveLength(1);
 }finally{g.close();}
});

test('a Realtime socket for a service saved as Off is refused at once, as before, with nothing woken',async()=>{
 const g=gatewayWith();
 try{
  const key=g.keys.issue(g.runtime).token;
  const decide=realtimeUpgrade({route:()=>g.resolve() as never,verifyKey:(runtime,candidate)=>g.keys.resolve(runtime,candidate),
   wake:{isOnDemand:runtime=>g.onDemand.isOnDemand(runtime,'realtime'),ensure:runtime=>g.onDemand.ensure(runtime,'realtime')}});
  const headers=new Headers({upgrade:'websocket',connection:'Upgrade','sec-websocket-key':'abc==','sec-websocket-version':'13'});
  expect(await decide(`/${g.runtime}/realtime/v1/websocket?apikey=${key}&vsn=1`,headers)).toMatchObject({ok:false,status:404});
  expect(row(g.catalog,g.runtime,'realtime')).toBeNull();
 }finally{g.close();}
});

test('sleep is judged at most once a minute, however often the sweeper looks',()=>{
 let now=10*60*MIN;
 const h=harness({now:()=>now});
 try{
  h.catalog.setServiceMode('alice',h.environment,'functions','on_demand',5);
  setState(h.catalog,h.runtime,'functions','on','on');h.running.add(h.runtime+'|functions');
  now+=60*MIN;
  expect(h.onDemand.sweep(now)).toEqual({slept:1,woke:0});
  setState(h.catalog,h.runtime,'functions','on','on');                                 // woken again at once (a request)
  now+=10_000;
  expect(h.onDemand.sweep(now)).toEqual({slept:0,woke:0});                             // ten seconds later: not yet
  now+=60_000;
  expect(h.onDemand.sweep(now)).toEqual({slept:1,woke:0});
 }finally{h.catalog.close();}
});

test('wakes made by the kill switch do not count against the hourly limit of a person or an app',async()=>{
 const h=harness();
 try{
  h.catalog.setServiceMode('alice',h.environment,'functions','on_demand');
  for(let index=0;index<WAKES_PER_HOUR+2;index++){
   expect(h.catalog.requestServiceChange(h.runtime,'functions',true,'system:kill-switch','kill_switch')).toBe('requested');
   setState(h.catalog,h.runtime,'functions','off','off');
  }
  expect(h.catalog.wakesInWindow(h.runtime,'functions')).toBe(0);
  expect(await h.onDemand.ensure(h.runtime,'functions')).toEqual({ok:true});
 }finally{h.catalog.close();}
});

test('a drain refuses new calls for a while and ends by itself; waiting for running calls stops at the timeout',async()=>{
 let now=1_000,inFlight=2;
 const activity=new ServiceActivity(undefined,()=>now,()=>inFlight);
 const R='e_'+'d'.repeat(24);
 expect(activity.isDraining(R)).toBe(false);
 activity.beginDrain(R,500);
 expect(activity.isDraining(R)).toBe(true);
 now+=499;expect(activity.isDraining(R)).toBe(true);
 now+=1;expect(activity.isDraining(R)).toBe(false);                                    // a stop that never comes cannot refuse calls for good
 // Real time for the wait: calls end after 60 ms.
 const real=new ServiceActivity(undefined,Date.now,()=>inFlight);
 setTimeout(()=>{inFlight=0;},60);
 expect(await real.waitForCalls(R,2000,10)).toBe(true);
 inFlight=1;
 expect(await real.waitForCalls(R,80,10)).toBe(false);
});

test('turning Edge Functions off waits for the calls that are running, and the gateway refuses new ones meanwhile',async()=>{
 const {functionsHandler}=await import('../src/control/functions');
 const g=gatewayWith();
 try{
  const key=g.keys.issue(g.runtime).token;
  setState(g.catalog,g.runtime,'functions','on','on');g.running.add(g.runtime+'|functions');
  let inFlight=1;
  const activity=new ServiceActivity(undefined,Date.now,()=>inFlight);
  const gate=new ConcurrencyGate();
  const gateway=managedGateway(g.catalog,g.keys,g.resolve as never,Object.assign(async()=>new Response('ok'),{preconnect:()=>{}}),gate,activity);
  const handler=functionsHandler(g.catalog,async()=>'alice',undefined,undefined,undefined,undefined,undefined,activity);
  const path=`http://local/management/v1/environments/${g.environment}/functions`;
  const started=Date.now();
  setTimeout(()=>{inFlight=0;},250);
  const response=handler(new Request(path,{method:'PUT',headers:{authorization:'alice','content-type':'application/json'},body:JSON.stringify({enabled:false})}));
  await Bun.sleep(50);
  // Mid drain: a call that has not started is told to retry, and nothing has been asked of the supervisor yet.
  const refused=await gateway(new Request(`http://local/${g.runtime}/functions/v1/hello`,{headers:{apikey:key}}));
  expect(refused.status).toBe(503);expect(refused.headers.get('retry-after')).toBe('2');
  expect(row(g.catalog,g.runtime,'functions')).toMatchObject({desired:'on',state:'on'});
  expect((await response).status).toBe(202);
  expect(Date.now()-started).toBeGreaterThanOrEqual(240);
  expect(row(g.catalog,g.runtime,'functions')).toMatchObject({desired:'off',state:'pending'});
  // Turning it on again is not a drain.
  setState(g.catalog,g.runtime,'functions','off','off');
  const quick=Date.now();
  await handler(new Request(path,{method:'PUT',headers:{authorization:'alice','content-type':'application/json'},body:JSON.stringify({enabled:true})}));
  expect(Date.now()-quick).toBeLessThan(200);
 }finally{g.close();}
});
