import {afterEach,expect,spyOn,test} from 'bun:test';
import type {Database} from 'bun:sqlite';
import {createHash,randomUUID} from 'node:crypto';
import {Catalog} from '../src/control/catalog';
import type {PasswordIdentity} from '../src/control/auth';
import {TransferInventoryStore,type InventoryCandidate,type TransferInventoryPorts} from '../src/control/transfer-store';
import {CurrentTransferHistoryQuery,type CurrentHistoryCapability,type CurrentHistoryProof,
 type TransferHistoryPorts,type TransferHistoryQuery} from '../src/control/transfer-history';

// All identity and history authority callbacks below are fixture providers.
// They prove source admission behavior only, never installed native retirement.
const actor='11111111-1111-4111-8111-111111111111';
const otherActor='22222222-2222-4222-8222-222222222222';
const session='33333333-3333-4333-8333-333333333333';
const factor='44444444-4444-4444-8444-444444444444';
const installation='55555555-5555-4555-8555-555555555555';
const operation='66666666-6666-4666-8666-666666666666';
const authority='77777777-7777-4777-8777-777777777777';
const sha=(value:string)=>createHash('sha256').update(value).digest('hex');
const catalogs:Catalog[]=[];
const clocks:ReturnType<typeof spyOn>[]=[];
afterEach(()=>{for(const clock of clocks.splice(0))clock.mockRestore();for(const catalog of catalogs.splice(0))catalog.close();});

function fixture(options:{tokenClaims?:Record<string,unknown>}={}) {
 const catalog=new Catalog(':memory:');catalogs.push(catalog);
 // Private access belongs only to this test fixture, not the production API.
 const db=(catalog as unknown as {db:Database}).db;
 const organization=catalog.createOrganization(actor,'Source fixture');
 const destination=catalog.createOrganization(otherActor,'Destination fixture');
 const project=catalog.createProject(actor,organization,'Transfer fixture');
 const environment=catalog.createEnvironment(actor,project,'production');
 const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
 const runtime=job.runtime;
 const now=Math.floor(Date.now()/1000);
 // This signature is harmless fixture data, accepted only by identify below.
 const tokenClaims={sub:actor,session_id:session,exp:now+3600,...options.tokenClaims};
 const token=Buffer.from(JSON.stringify({alg:'HS256',typ:'JWT'})).toString('base64url')+'.'+
  Buffer.from(JSON.stringify(tokenClaims)).toString('base64url')+'.fixture';
 const authorization='Bearer '+token;
 let identity:PasswordIdentity={actor,session,aal:'aal2',expiresAt:now+3600,passwordAt:now-5,verifiedAt:now-1,
  factors:[{id:factor,status:'verified',factor_type:'totp'}]};
 expect(catalog.managementSecurity.grant(actor,session,factor,identity.verifiedAt,now+3600,0)).toBe(true);
 const resource=randomUUID();
 const candidate=(generation:number):InventoryCandidate=>{
  const inventory=JSON.stringify([{kind:'container',id:sha('fixture-container-'+generation),installation,resource,runtime}]);
  return {inventory,inventoryDigest:sha(inventory),coverage:'disposable-fixture',placementDigest:sha('fixture-placement-'+generation)};
 };
 const inventoryPorts:TransferInventoryPorts={
  assertAdoption:()=>{},assertMutation:()=>{},
  validateManifest:(bound,value,coverage)=>{
   if(coverage!=='disposable-fixture'||JSON.parse(value).some((row:any)=>row.runtime!==bound))throw new Error('Fixture scope refused');
  },
  verifyReplacement:async({mutation,candidate})=>({...mutation,candidateDigest:candidate.inventoryDigest,
   candidatePlacementDigest:candidate.placementDigest,coverage:candidate.coverage,proofDigest:sha('fixture-replacement')})
 };
 const initial=candidate(0);
 catalog.registerLifecycleResources(runtime,JSON.parse(initial.inventory),initial.coverage);
 catalog.deleteEnvironment(actor,environment);
 const deleting=catalog.lifecycle(actor,environment)!;
 // Explicit disposable Source effects exercise the real Catalog producer without native admission.
 catalog.lifecycleEffect(deleting,resource,'fixture-stopped');
 catalog.finishLifecycle(deleting);
 const restoring=catalog.restoreEnvironment(actor,environment,operation),sourceEpoch=restoring.epoch;
 expect(restoring.state).toBe('restoring');expect(restoring.runtime).toBe(runtime);
 // The real lifecycle producer creates restoring state. This disposable Source fixture
 // supplies its private routing fence; native fence admission is tested separately.
 db.query('INSERT INTO runtime_routing(runtime,revision,maintenance,placement) VALUES (?,1,1,NULL)').run(runtime);
 const inventory=new TransferInventoryStore(db,inventoryPorts);
 db.transaction(()=>inventory.adoptInTransaction({runtime,environment,installation,generation:0,epoch:sourceEpoch,
  digestVersion:'sb07-json-v1',...initial}))();
 const input:TransferHistoryQuery={installation,project,environment,runtime,operation,managementEpoch:0,
  sourceGeneration:0,sourceEpoch,sourceInventoryDigest:initial.inventoryDigest,sourcePlacementDigest:initial.placementDigest};
 let identified=0,verified=0,asserted=0,trusted=true,ledgerRevision=1,ledgerDigest=sha('fixture-ledger-1');
 let identifyHook:(call:number)=>void|Promise<void>=()=>{};
 let verifyHook:()=>void|Promise<void>=()=>{};
 let assertHook:()=>unknown=()=>undefined;
 let proofChange:Partial<CurrentHistoryProof>&Record<string,unknown>={};
 let returnedProof:CurrentHistoryProof|undefined;
 const ports:TransferHistoryPorts={authority,
  identify:async request=>{
   expect(db.inTransaction).toBe(false);expect(request.headers.get('authorization')).toBe(authorization);
   await identifyHook(++identified);return structuredClone(identity);
  },
  verifyCurrentHistory:async snapshot=>{
   expect(db.inTransaction).toBe(false);verified++;
   if(!trusted)throw new Error('Independent fixture authority refuses restored Catalog');
   const proof={authority,ledgerRevision,ledgerDigest,bindingDigest:snapshot.bindingDigest,
    proofDigest:sha('fixture-proof-'+snapshot.bindingDigest),expiresAt:Date.now()+10000,...proofChange};
   returnedProof=proof;await verifyHook();return proof;
  },
  assertCurrentHistory:proof=>{
   expect(db.inTransaction).toBe(true);asserted++;
   if(!trusted||proof.ledgerRevision!==ledgerRevision||proof.ledgerDigest!==ledgerDigest)throw new Error('Independent authority changed');
   return assertHook() as void;
  }
 };
 const query=new CurrentTransferHistoryQuery(db,inventory,ports);
 const request=()=>new Request('http://fixture.invalid/history',{headers:{authorization}});
 const run=(changed:Partial<TransferHistoryQuery>={})=>query.query(request(),{...input,...changed});
 const check=(capability:CurrentHistoryCapability)=>db.transaction(()=>query.assertCurrentInTransaction(capability)).immediate();
 const append=async()=>{
  const previous=inventory.head(runtime)!,next=candidate(previous.generation+1);
  const cap=await inventory.verifyPublication({operation:randomUUID(),runtime,environment,installation,
   sourceGeneration:previous.generation,sourceEpoch:previous.epoch,heldEpoch:previous.epoch+1,
   previousDigest:previous.inventoryDigest,sourcePlacementDigest:previous.placementDigest,heldPlacementDigest:sha('fixture-held')},next);
  db.transaction(()=>{
   inventory.appendInTransaction(cap);
   db.query('UPDATE environment_lifecycle SET epoch=?,inventory=?,coverage=? WHERE environment=?')
    .run(previous.epoch+1,next.inventory,next.coverage,environment);
  })();
 };
 const corrupt=(sql:string,...values:any[])=>{
  // Explicit corruption fixture bypasses immutable triggers, never a supported writer.
  db.exec('DROP TRIGGER IF EXISTS transfer_inventory_no_update; DROP TRIGGER IF EXISTS transfer_inventory_no_delete');
  db.query(sql).run(...values);
 };
 return {catalog,db,organization,destination,project,environment,runtime,input,identity,inventory,inventoryPorts,ports,query,
  tokenClaims,authorization,
  request,run,check,append,corrupt,counts:()=>({identified,verified,asserted}),
  auth:(change:Partial<PasswordIdentity>)=>{identity={...identity,...change};},
  identify:(hook:typeof identifyHook)=>{identifyHook=hook;},verify:(hook:typeof verifyHook)=>{verifyHook=hook;},
  assertion:(hook:typeof assertHook)=>{assertHook=hook;},proof:(change:typeof proofChange)=>{proofChange=change;},
  returnedProof:()=>returnedProof!,
  trust:(value:boolean)=>{trusted=value;},ledger:()=>{ledgerRevision++;ledgerDigest=sha('fixture-ledger-'+ledgerRevision);}};
}

test('fixture consultation identifies twice and never admits native retirement',async()=>{
 const f=fixture(),result=await f.run();expect(f.counts()).toEqual({identified:2,verified:1,asserted:1});
 expect(result.history.actor).toBe(actor);expect(result.history.organization).toBe(f.organization);
 expect(result.history.nativeRetirementAdmitted).toBe(false);expect(result.capability.nativeRetirementAdmitted).toBe(false);
 expect(result.history.generations).toHaveLength(1);expect(f.check(result.capability)).toEqual(result.history);
});
test('all intervening immutable generations are returned including retired resource identities',async()=>{
 const f=fixture();await f.append();await f.append();const {history}=await f.run();
 expect(history.generations.map(row=>row.generation)).toEqual([0,1,2]);expect(history.head.generation).toBe(2);
 expect(history.source.generation).toBe(0);expect(history.generations[1]!.previousDigest).toBe(history.source.inventoryDigest);
 expect(new Set(history.generations.map(row=>row.inventory)).size).toBe(3);
});
test('authorization header is required before identity or history providers run',async()=>{
 const f=fixture();for(const header of ['', 'Basic fixture','Bearer two tokens','Bearer '+ 'a'.repeat(8192)]){
  await expect(f.query.query(new Request('http://fixture.invalid',{headers:{authorization:header}}),f.input))
   .rejects.toThrow('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');
 }expect(f.counts().identified).toBe(0);
});
test('invalid and extended public queries refuse before authentication',async()=>{
 const f=fixture();for(const changed of [{runtime:f.runtime+'\n'},{operation:'00000000-0000-0000-0000-000000000000'},
  {managementEpoch:-1},{sourceGeneration:0.5},{sourceInventoryDigest:'a'.repeat(64)+'\n'}])
  await expect(f.run(changed)).rejects.toThrow('HISTORY_QUERY_INVALID');
 await expect(f.query.query(f.request(),{...f.input,installedNativeProof:true} as TransferHistoryQuery)).rejects.toThrow('HISTORY_SHAPE_INVALID');
 expect(f.counts().identified).toBe(0);
});
test('live authentication cannot be started inside a Catalog transaction',async()=>{
 const f=fixture();let pending:ReturnType<typeof f.run>|undefined;f.db.transaction(()=>{pending=f.run();})();
 await expect(pending!).rejects.toThrow('LIVE_AUTHENTICATION_OUTSIDE_TRANSACTION_REQUIRED');expect(f.counts().identified).toBe(0);
});
test('aal1 expired password and future native identity timestamps refuse',async()=>{
 for(const change of [{aal:'aal1' as const},{passwordAt:Math.floor(Date.now()/1000)-43200},
  {passwordAt:Math.floor(Date.now()/1000)+60},{verifiedAt:Math.floor(Date.now()/1000)+60}]){
  const f=fixture();f.auth(change);await expect(f.run()).rejects.toThrow('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');
  expect(f.counts().verified).toBe(0);
 }
});
test('native identity requires a current verified matching totp factor',async()=>{
 for(const factors of [[],[{id:factor,status:'unverified',factor_type:'totp'}],
  [{id:factor,status:'verified',factor_type:'phone'}],[{id:otherActor,status:'verified',factor_type:'totp'}]]){
  const f=fixture();f.auth({factors});await expect(f.run()).rejects.toThrow('CURRENT_MANAGEMENT_AUTHORITY_REFUSED');
 }
});
test('MFA grant expires at the exact current second',async()=>{
 const f=fixture();f.db.query('UPDATE management_mfa_grant SET expires=?').run(Math.floor(Date.now()/1000));
 await expect(f.run()).rejects.toThrow('CURRENT_MANAGEMENT_AUTHORITY_REFUSED');
});
test('native aal2 alone cannot replace the persisted management grant',async()=>{
 const f=fixture();f.db.exec('DELETE FROM management_mfa_grant');await expect(f.run()).rejects.toThrow('CURRENT_MANAGEMENT_AUTHORITY_REFUSED');
});
test('revocation and management epoch changes refuse old grants and claims',async()=>{
 const f=fixture();f.catalog.managementSecurity.revoke(actor);
 await expect(f.run()).rejects.toThrow('CURRENT_MANAGEMENT_AUTHORITY_REFUSED');
 const g=fixture();g.db.query('UPDATE management_mfa_grant SET epoch=1').run();
 await expect(g.run()).rejects.toThrow('CURRENT_MANAGEMENT_AUTHORITY_REFUSED');
});
test('admin and viewer memberships cannot consult owner transfer history',async()=>{
 for(const role of ['admin','viewer']){const f=fixture();f.db.query('UPDATE memberships SET role=? WHERE actor=?').run(role,actor);
  await expect(f.run()).rejects.toThrow('CURRENT_OPERATION_AUTHORITY_REFUSED');}
});
test('cross organization actor cannot use another owners operation or grant',async()=>{
 const f=fixture({tokenClaims:{sub:otherActor}});f.auth({actor:otherActor});
 f.catalog.managementSecurity.grant(otherActor,session,factor,f.identity.verifiedAt,Math.floor(Date.now()/1000)+3600,0);
 await expect(f.run()).rejects.toThrow('CURRENT_OPERATION_AUTHORITY_REFUSED');
});
test('project environment runtime and installation scope cannot be caller retargeted',async()=>{
 for(const change of [{project:otherActor},{environment:otherActor},{runtime:'e_'+ 'f'.repeat(24)},{installation:otherActor}]){
  const f=fixture();await expect(f.run(change)).rejects.toThrow();expect(f.counts().verified).toBe(0);
 }
});
test('live lifecycle must retain exact restoring operation actor and management epoch',async()=>{
 for(const [column,value] of [['state','active'],['operation',otherActor],['actor',otherActor],['management_epoch',1]] as const){
  const f=fixture();f.db.query('UPDATE environment_lifecycle SET '+column+'=?').run(value);
  await expect(f.run()).rejects.toThrow('CURRENT_OPERATION_AUTHORITY_REFUSED');
 }
});
test('provision readiness and lifecycle row must still exist',async()=>{
 const f=fixture();f.db.exec("UPDATE provision_jobs SET state='failed'");await expect(f.run()).rejects.toThrow('CURRENT_OPERATION_AUTHORITY_REFUSED');
 const g=fixture();g.db.exec('DELETE FROM environment_lifecycle');await expect(g.run()).rejects.toThrow('CURRENT_OPERATION_AUTHORITY_REFUSED');
});
test('unfenced or missing runtime routing refuses consultation',async()=>{
 const f=fixture();f.db.exec('UPDATE runtime_routing SET maintenance=0');await expect(f.run()).rejects.toThrow('CURRENT_RUNTIME_FENCE_REQUIRED');
 const g=fixture();g.db.exec('DELETE FROM runtime_routing');await expect(g.run()).rejects.toThrow('CURRENT_RUNTIME_FENCE_REQUIRED');
});
test('live lifecycle epoch inventory and coverage must include the current head',async()=>{
 for(const column of ['epoch','inventory','coverage'] as const){
  const f=fixture(),value={epoch:f.input.sourceEpoch-1,inventory:'[]',coverage:'dedicated-resources'}[column];
  f.db.query('UPDATE environment_lifecycle SET '+column+'=?').run(value);
  await expect(f.run()).rejects.toThrow('CURRENT_HISTORY_INCOMPLETE');
 }
});
test('restored apparently coherent Catalog is refused by independent authority',async()=>{
 const f=fixture();f.trust(false);await expect(f.run()).rejects.toThrow('CURRENT_HISTORY_AUTHORITY_REFUSED');
 expect(f.inventory.history(f.runtime)).toHaveLength(1);expect(f.counts().identified).toBe(1);
});
test('claimed source generation epoch inventory and placement require exact historical agreement',async()=>{
 for(const changed of [{sourceGeneration:1},{sourceEpoch:3},{sourceInventoryDigest:'a'.repeat(64)},{sourcePlacementDigest:'b'.repeat(64)}]){
  const f=fixture();await expect(f.run(changed)).rejects.toThrow();expect(f.counts().verified).toBe(0);
 }
});
test('append only history triggers refuse supported updates and deletion',()=>{
 const f=fixture();expect(()=>f.db.exec('UPDATE transfer_inventory_generations SET epoch=1')).toThrow('immutable');
 expect(()=>f.db.exec('DELETE FROM transfer_inventory_generations')).toThrow('immutable');
});
test('missing middle generation cannot be hidden by a coherent current head',async()=>{
 const f=fixture();await f.append();await f.append();f.corrupt('DELETE FROM transfer_inventory_generations WHERE generation=1');
 await expect(f.run()).rejects.toThrow('CURRENT_HISTORY_INCOMPLETE');
});
test('every intervening generation must chain exact digest and monotone epoch',async()=>{
 for(const column of ['previous_digest','epoch','installation','proof_digest'] as const){
  const f=fixture();await f.append();await f.append();
  const value={previous_digest:'a'.repeat(64),epoch:f.input.sourceEpoch-1,installation:otherActor,proof_digest:'bad'}[column];
  f.corrupt('UPDATE transfer_inventory_generations SET '+column+'=? WHERE generation=1',value);
  await expect(f.run()).rejects.toThrow('CURRENT_HISTORY_INCOMPLETE');
 }
});
test('corrupted inventory bytes and alternate JSON encoding refuse even if rehashed',async()=>{
 for(const inventory of ['bad json',' [1] ','[{"a":1,"a":1}]']){
  const f=fixture();await f.append();await f.append();
  f.corrupt('UPDATE transfer_inventory_generations SET inventory=?,inventory_digest=? WHERE generation=1',inventory,sha(inventory));
  await expect(f.run()).rejects.toThrow();
 }
});
test('valid JSON byte ordering remains part of the historical source claim',async()=>{
 const f=fixture(),row=f.inventory.head(f.runtime)!;
 const resources=JSON.parse(row.inventory)[0],inventory=JSON.stringify([{runtime:resources.runtime,id:resources.id,type:resources.type}]);
 f.corrupt('UPDATE transfer_inventory_generations SET inventory=?,inventory_digest=?',inventory,sha(inventory));
 f.db.query('UPDATE environment_lifecycle SET inventory=?').run(inventory);
 await expect(f.run()).rejects.toThrow('CLAIMED_SOURCE_HISTORY_REFUSED');
});
test('history total byte budget is checked before provider observation',async()=>{
 const f=fixture();f.corrupt('UPDATE transfer_inventory_generations SET inventory=?','x'.repeat(2*1024*1024+1));
 await expect(f.run()).rejects.toThrow('CURRENT_HISTORY_INCOMPLETE');expect(f.counts().verified).toBe(0);
});
test('history generation budget refuses 129 rows before provider observation',async()=>{
 const f=fixture();for(let generation=1;generation<=128;generation++)f.db.query(`INSERT INTO transfer_inventory_generations
  SELECT runtime,?,environment,installation,epoch,digest_version,inventory,inventory_digest,coverage,placement_digest,?,previous_digest,proof_digest
  FROM transfer_inventory_generations WHERE generation=0`).run(generation,randomUUID());
 await expect(f.run()).rejects.toThrow('CURRENT_HISTORY_INCOMPLETE');expect(f.counts().verified).toBe(0);
});
test('head deletion and head ordering corruption refuse complete history claims',async()=>{
 const f=fixture();f.db.exec('DELETE FROM transfer_inventory_heads');await expect(f.run()).rejects.toThrow('CURRENT_HISTORY_INCOMPLETE');
 const g=fixture();await g.append();g.db.exec('UPDATE transfer_inventory_heads SET generation=0');
 await expect(g.run()).rejects.toThrow('CURRENT_HISTORY_INCOMPLETE');
});
test('independent proof is exact bound scoped current and credential free',async()=>{
 for(const change of [{authority:otherActor},{bindingDigest:'a'.repeat(64)},{ledgerRevision:-1},{ledgerDigest:'bad'},
  {proofDigest:'bad'},{expiresAt:Date.now()-1},{secret:'forbidden-extra-field'}]){
  const f=fixture();f.proof(change);await expect(f.run()).rejects.toThrow();expect(f.counts().identified).toBe(1);
 }
});
test('fresh second identify refuses changed actor session and verification timestamp',async()=>{
 for(const changed of [{actor:otherActor},{session:otherActor},{verifiedAt:Math.floor(Date.now()/1000)-2}]){
  const f=fixture();f.identify(call=>{if(call===2)f.auth(changed);});
  await expect(f.run()).rejects.toThrow('CURRENT_MANAGEMENT_AUTHORITY_CHANGED');
 }
});
test('fresh second identify must retain matching current verified factor',async()=>{
 const f=fixture();f.identify(call=>{if(call===2)f.auth({factors:[]});});
 await expect(f.run()).rejects.toThrow('CURRENT_MANAGEMENT_AUTHORITY_REFUSED');
});
test('MFA revocation during awaited independent proof refuses publication capability',async()=>{
 const f=fixture();f.verify(()=>f.catalog.managementSecurity.revoke(actor));
 await expect(f.run()).rejects.toThrow('CURRENT_MANAGEMENT_AUTHORITY_REFUSED');
});
test('independent ledger and authority changes across awaited proof are refused',async()=>{
 const f=fixture();f.verify(()=>f.ledger());await expect(f.run()).rejects.toThrow('CURRENT_HISTORY_AUTHORITY_REFUSED');
 const g=fixture();g.verify(()=>g.trust(false));await expect(g.run()).rejects.toThrow('CURRENT_HISTORY_AUTHORITY_REFUSED');
});
test('owner and restoring operation changes across awaited proof are refused',async()=>{
 for(const mutate of [(f:ReturnType<typeof fixture>)=>f.db.query('UPDATE memberships SET role=? WHERE actor=?').run('viewer',actor),
  (f:ReturnType<typeof fixture>)=>f.db.query('UPDATE environment_lifecycle SET operation=?').run(otherActor)]){
  const f=fixture();f.verify(()=>{mutate(f);});await expect(f.run()).rejects.toThrow('CURRENT_OPERATION_AUTHORITY_REFUSED');
 }
});
test('head append and routing revision changes across awaited proof invalidate its binding',async()=>{
 const f=fixture();f.verify(()=>f.append());await expect(f.run()).rejects.toThrow('CURRENT_HISTORY_CHANGED');
 const g=fixture();g.verify(()=>{g.db.exec('UPDATE runtime_routing SET revision=revision+1');});
 await expect(g.run()).rejects.toThrow('CURRENT_HISTORY_CHANGED');
});
test('async authority assertion cannot be treated as a transaction guard',async()=>{
 const f=fixture();f.assertion(()=>Promise.resolve());await expect(f.run()).rejects.toThrow('CURRENT_HISTORY_AUTHORITY_REFUSED');
 const g=fixture();g.assertion(()=>Promise.reject(new Error('private fixture refusal')));
 await expect(g.run()).rejects.toThrow('CURRENT_HISTORY_AUTHORITY_REFUSED');
});
test('copied forged and other query capabilities cannot authorize publication',async()=>{
 const f=fixture(),{capability}=await f.run();
 for(const forged of [{...capability},JSON.parse(JSON.stringify(capability)),null,'receipt'])
  expect(()=>f.check(forged as CurrentHistoryCapability)).toThrow('AUTHENTICATED_HISTORY_CAPABILITY_REQUIRED');
 const reopened=new CurrentTransferHistoryQuery(f.db,f.inventory,f.ports);
 expect(()=>f.db.transaction(()=>reopened.assertCurrentInTransaction(capability))()).toThrow('AUTHENTICATED_HISTORY_CAPABILITY_REQUIRED');
 const otherStore=new TransferInventoryStore(f.db,f.inventoryPorts),other=new CurrentTransferHistoryQuery(f.db,otherStore,f.ports);
 expect(()=>f.db.transaction(()=>other.assertCurrentInTransaction(capability))()).toThrow('AUTHENTICATED_HISTORY_CAPABILITY_REQUIRED');
});
test('capabilities require final transaction and exact Catalog connection',async()=>{
 const f=fixture(),g=fixture(),{capability}=await f.run();
 expect(()=>f.query.assertCurrentInTransaction(capability)).toThrow('CATALOG_TRANSACTION_REQUIRED');
 expect(()=>new CurrentTransferHistoryQuery(g.db,f.inventory,f.ports)).toThrow('LIVE_CATALOG_CONNECTION_REQUIRED');
});
test('expired capability cannot survive until a later ownership transaction',async()=>{
 const f=fixture(),{capability}=await f.run(),now=Date.now();clocks.push(spyOn(Date,'now').mockReturnValue(now+20000));
 expect(()=>f.check(capability)).toThrow('CURRENT_HISTORY_PROOF_EXPIRED');
});
test('final synchronous guard rechecks independent ledger and async refusal',async()=>{
 const f=fixture(),{capability}=await f.run();f.ledger();expect(()=>f.check(capability)).toThrow('CURRENT_HISTORY_AUTHORITY_REFUSED');
 const g=fixture(),result=await g.run();g.assertion(()=>Promise.resolve());
 expect(()=>g.check(result.capability)).toThrow('CURRENT_HISTORY_AUTHORITY_REFUSED');
});
test('final transaction rechecks live grant owner operation and routing',async()=>{
 for(const mutate of [(f:ReturnType<typeof fixture>)=>f.catalog.managementSecurity.revoke(actor),
  (f:ReturnType<typeof fixture>)=>f.db.query('UPDATE memberships SET role=? WHERE actor=?').run('admin',actor),
  (f:ReturnType<typeof fixture>)=>f.db.query('UPDATE environment_lifecycle SET operation=?').run(otherActor),
  (f:ReturnType<typeof fixture>)=>f.db.exec('UPDATE runtime_routing SET revision=revision+1')]){
  const f=fixture(),{capability}=await f.run();mutate(f);expect(()=>f.check(capability)).toThrow();
 }
});
test('owner publication rolls back if the final authority guard refuses',async()=>{
 const f=fixture(),{capability}=await f.run();
 expect(()=>f.db.transaction(()=>{
  f.db.query('UPDATE projects SET organization=? WHERE id=?').run(f.destination,f.project);
  f.query.assertCurrentInTransaction(capability);
 })()).toThrow('CURRENT_OPERATION_AUTHORITY_REFUSED');
 expect(f.catalog.listProjects(actor,f.organization).map(project=>project.id)).toEqual([f.project]);
 expect(f.inventory.head(f.runtime)?.generation).toBe(0);
});
test('late publication failure rolls back ownership after a successful final guard',async()=>{
 const f=fixture(),{capability}=await f.run();expect(()=>f.db.transaction(()=>{
  f.query.assertCurrentInTransaction(capability);f.db.query('UPDATE projects SET organization=? WHERE id=?').run(f.destination,f.project);
  throw new Error('Fixture publication failure');
 })()).toThrow('Fixture publication failure');
 expect(f.catalog.listProjects(actor,f.organization).map(project=>project.id)).toEqual([f.project]);
});
test('caller query identity proof and returned history mutation cannot retarget authority',async()=>{
 const f=fixture(),input={...f.input},pending=f.query.query(f.request(),input);input.operation=otherActor;
 const result=await pending;f.identity.factors[0]!.status='unverified';f.returnedProof().expiresAt=0;f.returnedProof().ledgerRevision=-1;
 expect(result.history.query.operation).toBe(operation);expect(Object.isFrozen(result.history.query)).toBe(true);
 expect(Object.isFrozen(result.history.generations)).toBe(true);expect(Object.isFrozen(result.history.head)).toBe(true);
 expect(Reflect.set(result.capability,'operation',otherActor)).toBe(false);
 expect(Reflect.set(result.history.generations[0]!,'inventory','[]')).toBe(false);
 expect(f.check(result.capability).query.operation).toBe(operation);
});
test('uninstalled independent provider cannot be synthesized from copied receipts',()=>{
 const f=fixture();for(const changed of [{authority:'bad'},{identify:undefined},{verifyCurrentHistory:undefined},{assertCurrentHistory:undefined}])
  expect(()=>new CurrentTransferHistoryQuery(f.db,f.inventory,{...f.ports,...changed} as TransferHistoryPorts))
   .toThrow('CURRENT_HISTORY_PROVIDER_UNINSTALLED');
});
test('provider errors and null native identities are refused without diagnostic disclosure',async()=>{
 const f=fixture();f.identify(()=>{throw new Error('private bearer fixture details');});
 await expect(f.run()).rejects.toThrow('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');
 const g=fixture(),nullIdentity=new CurrentTransferHistoryQuery(g.db,g.inventory,{...g.ports,identify:async()=>null});
 await expect(nullIdentity.query(g.request(),g.input)).rejects.toThrow('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');
});
test('mutating caller owned installed ports cannot replace the admitted authority',async()=>{
 const f=fixture();f.ports.identify=async()=>null;f.ports.assertCurrentHistory=()=>{throw new Error('replacement fixture');};
 const {capability}=await f.run();expect(f.check(capability).nativeRetirementAdmitted).toBe(false);
});
test('expired fixture token is refused after trusted identify before history observation',async()=>{
 const f=fixture({tokenClaims:{exp:Math.floor(Date.now()/1000)}});
 await expect(f.run()).rejects.toThrow('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');
 expect(f.counts()).toEqual({identified:1,verified:0,asserted:0});
});
test('token subject and session must match the trusted original identity',async()=>{
 for(const tokenClaims of [{sub:otherActor},{session_id:otherActor},{sub:null},{session_id:null}]){
  const f=fixture({tokenClaims});await expect(f.run()).rejects.toThrow('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');
  expect(f.counts().verified).toBe(0);
 }
});
test('token expiry must be an explicit safe positive integer',async()=>{
 for(const exp of [undefined,null,'9999999999',0,-1,Number.MAX_SAFE_INTEGER+1,Math.floor(Number.MAX_SAFE_INTEGER/1000)+1,1.5]){
  const f=fixture({tokenClaims:{exp}});await expect(f.run()).rejects.toThrow('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');
  expect(f.counts().verified).toBe(0);
 }
});
test('token expiry across awaited independent proof prevents issuing a capability',async()=>{
 const exp=Math.floor(Date.now()/1000)+2,f=fixture({tokenClaims:{exp}});
 f.verify(()=>{clocks.push(spyOn(Date,'now').mockReturnValue(exp*1000));});
 await expect(f.run()).rejects.toThrow('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');
 expect(f.counts().verified).toBe(1);expect(f.inventory.head(f.runtime)?.generation).toBe(0);
});
test('token expiry after consultation refuses the final transaction despite a live proof and grant',async()=>{
 const exp=Math.floor(Date.now()/1000)+2,f=fixture({tokenClaims:{exp}}),{capability}=await f.run();
 expect(f.returnedProof().expiresAt).toBeGreaterThan(exp*1000);
 clocks.push(spyOn(Date,'now').mockReturnValue(exp*1000));
 expect(()=>f.check(capability)).toThrow('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');
 expect(f.catalog.managementSecurity.granted(actor,session,f.identity.verifiedAt,[factor])).toBe(true);
});
test('grant expiry after consultation refuses the final transaction despite a live token and proof',async()=>{
 const f=fixture(),{capability}=await f.run();
 f.db.query('UPDATE management_mfa_grant SET expires=?').run(Math.floor(Date.now()/1000));
 expect(()=>f.check(capability)).toThrow('CURRENT_MANAGEMENT_AUTHORITY_REFUSED');
 expect(f.tokenClaims.exp).toBeGreaterThan(Math.floor(Date.now()/1000));
});
test('token expiry refusal rolls back attempted ownership publication',async()=>{
 const exp=Math.floor(Date.now()/1000)+2,f=fixture({tokenClaims:{exp}}),{capability}=await f.run();
 clocks.push(spyOn(Date,'now').mockReturnValue(exp*1000));
 expect(()=>f.db.transaction(()=>{
  f.db.query('UPDATE projects SET organization=? WHERE id=?').run(f.destination,f.project);
  f.query.assertCurrentInTransaction(capability);
 })()).toThrow('CURRENT_MANAGEMENT_AUTHENTICATION_REQUIRED');
 expect(f.catalog.listProjects(actor,f.organization).map(project=>project.id)).toEqual([f.project]);
 expect(f.inventory.head(f.runtime)?.generation).toBe(0);
});
test('history binds a hashed native session and earliest token or MFA expiry without retaining bearer material',async()=>{
 const exp=Math.floor(Date.now()/1000)+120,f=fixture({tokenClaims:{exp}}),{history}=await f.run();
 expect(history.sessionDigest).toBe(sha(session));expect(history.authenticationExpiresAt).toBe(exp*1000);
 const publicHistory=JSON.stringify(history);expect(publicHistory).not.toContain(session);expect(publicHistory).not.toContain(f.authorization);
 const g=fixture(),grantExpiry=Math.floor(Date.now()/1000)+60;
 g.db.query('UPDATE management_mfa_grant SET expires=?').run(grantExpiry);
 expect((await g.run()).history.authenticationExpiresAt).toBe(grantExpiry*1000);
});
test('still live MFA expiry changes across awaited proof invalidate its authentication binding',async()=>{
 const f=fixture();f.verify(()=>{f.db.query('UPDATE management_mfa_grant SET expires=?').run(Math.floor(Date.now()/1000)+120);});
 await expect(f.run()).rejects.toThrow('CURRENT_HISTORY_CHANGED');
});
test('still live MFA expiry changes before final publication invalidate the capability binding',async()=>{
 const f=fixture(),{capability}=await f.run();
 f.db.query('UPDATE management_mfa_grant SET expires=?').run(Math.floor(Date.now()/1000)+120);
 expect(()=>f.check(capability)).toThrow('CURRENT_HISTORY_CHANGED');
});
