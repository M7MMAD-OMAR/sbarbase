import {test,expect} from 'bun:test';
import {sameTransferBinding,validateTransferBinding,type TransferRuntimeBinding} from '../src/control/transfer-contract';

const runtime='e_0123456789abcdef01234567';
function binding():TransferRuntimeBinding {
 return {operation:'11111111-1111-4111-8111-111111111111',project:'22222222-2222-4222-8222-222222222222',
  source:'33333333-3333-4333-8333-333333333333',destination:'44444444-4444-4444-8444-444444444444',
  environment:'55555555-5555-4555-8555-555555555555',runtime,actor:'66666666-6666-4666-8666-666666666666',
  management_epoch:0,runtime_epoch:1,inventory_digest:'a'.repeat(64),installation:'77777777-7777-4777-8777-777777777777',
  daemon_digest:'b'.repeat(64),engine_id:'c'.repeat(64),database_oid:'1234',
  roles:{[runtime+'_auth']:'1235',[runtime+'_rest']:'1236',[runtime+'_storage']:'1237'},routing_revision:3};
}

test('admitted identity is copied and frozen independently of the caller',()=>{
 const original=binding(),admitted=validateTransferBinding(original);
 original.roles[runtime+'_auth']='9999';original.runtime_epoch=2;
 expect(admitted.roles[runtime+'_auth']).toBe('1235');expect(admitted.runtime_epoch).toBe(1);
 expect(Object.isFrozen(admitted)).toBe(true);expect(Object.isFrozen(admitted.roles)).toBe(true);
});

test('physical identity substitutions do not match an admitted operation',()=>{
 const original=binding();
 for(const changed of [
  {...original,engine_id:'d'.repeat(64)}, {...original,daemon_digest:'e'.repeat(64)},
  {...original,database_oid:'9999'}, {...original,routing_revision:4},
  {...original,roles:{...original.roles,[runtime+'_storage']:'9998'}},
  {...original,inventory_digest:'f'.repeat(64)}, {...original,runtime_epoch:2},
 ])expect(sameTransferBinding(original,changed)).toBe(false);
});

test('replay material belongs to its initial authorization and destination',()=>{
 const original=binding();
 expect(sameTransferBinding(original,{...original,management_epoch:1})).toBe(false);
 expect(sameTransferBinding(original,{...original,actor:'88888888-8888-4888-8888-888888888888'})).toBe(false);
 expect(sameTransferBinding(original,{...original,destination:'99999999-9999-4999-8999-999999999999'})).toBe(false);
});

test('role ordering cannot create a second credential generation',()=>{
 const original=binding();
 const reversed={...original,roles:Object.fromEntries(Object.entries(original.roles).reverse())};
 expect(sameTransferBinding(original,reversed)).toBe(true);
});

test('crossed tenant role names and aliased role identities are rejected',()=>{
 const original=binding();
 expect(()=>validateTransferBinding({...original,roles:{...original.roles,e_ffffffffffffffffffffffff_auth:'9876'}})).toThrow('Invalid transfer roles');
 expect(()=>validateTransferBinding({...original,roles:{...original.roles,[runtime+'_storage']:'1235'}})).toThrow('Invalid transfer roles');
 expect(()=>validateTransferBinding({...original,roles:{[runtime+'_auth']:'1235',[runtime+'_rest']:'1236'}})).toThrow('Invalid transfer roles');
});

test('native OIDs are canonical bounded values rather than SQL fragments',()=>{
 const original=binding();
 for(const value of ['0','01','-1','4294967296','1;SELECT 1',' 1234','1234\n'])
  expect(()=>validateTransferBinding({...original,database_oid:value})).toThrow();
 for(const value of ['0','01','4294967296','1235;SELECT 1'])
  expect(()=>validateTransferBinding({...original,roles:{...original.roles,[runtime+'_auth']:value}})).toThrow();
});

test('private or unexpected fields cannot enter a public binding',()=>{
 const original=binding();
 for(const field of ['password','jwt','authorization','credentials','previous_secret','receipt'])
  expect(()=>validateTransferBinding({...original,[field]:'synthetic-value'})).toThrow('Invalid transfer binding');
});

test('epochs and routing revisions cannot be fractional, negative or exhausted',()=>{
 const original=binding();
 for(const field of ['management_epoch','runtime_epoch','routing_revision'])
  for(const value of [-1,1.5,NaN,Infinity,Number.MAX_SAFE_INTEGER+1])
   expect(()=>validateTransferBinding({...original,[field]:value})).toThrow();
 expect(()=>validateTransferBinding({...original,runtime_epoch:0})).toThrow();
});

test('intent cannot change directory scope through identifiers or runtime names',()=>{
 const original=binding();
 for(const field of ['operation','project','source','destination','environment','installation','actor'])
  for(const value of ['../other','synthetic-user','00000000-0000-0000-0000-000000000000',
   '11111111-1111-4111-8111-111111111111/child','11111111-1111-4111-8111-111111111111\n'])
   expect(()=>validateTransferBinding({...original,[field]:value})).toThrow();
 for(const value of [runtime+'/child','../'+runtime,'e_ffffffffffffffffffffffff\n'])
  expect(()=>validateTransferBinding({...original,runtime:value})).toThrow();
});

test('same organization intent and malformed native identities refuse before reservation',()=>{
 const original=binding();
 expect(()=>validateTransferBinding({...original,destination:original.source})).toThrow();
 for(const field of ['inventory_digest','daemon_digest','engine_id'])
  for(const value of ['bad','d'.repeat(63),'d'.repeat(64)+'\n'])
   expect(()=>validateTransferBinding({...original,[field]:value})).toThrow();
});
