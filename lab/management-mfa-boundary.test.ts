import {describe,expect,test} from 'bun:test';
import {nativeVerificationBoundary} from './management-mfa-boundary';

const endpoint=new URL('http://native.fixture/factors/factor/verify');
describe('native verification interleaving boundary',()=>{
 test('waits for intervention after successful upstream response and preserves original body',async()=>{
  const events:string[]=[];
  let release!:()=>void;
  const gate=new Promise<void>(resolve=>{release=resolve;});
  const wrapped=nativeVerificationBoundary(async()=>{events.push('upstream');return Response.json({access_token:'source-test-only'});},endpoint,async(response)=>{
   expect(await response.json()).toEqual({access_token:'source-test-only'});events.push('intervention');await gate;events.push('released');
  });
  let settled=false;
  const pending=wrapped(endpoint,{method:'POST'}).then(response=>{settled=true;return response;});
  for(let attempt=0;attempt<20&&!events.includes('intervention');attempt++)await Bun.sleep(1);
  expect(events).toEqual(['upstream','intervention']);expect(settled).toBe(false);
  release();expect(await(await pending).json()).toEqual({access_token:'source-test-only'});
  expect(events).toEqual(['upstream','intervention','released']);
 });
 test('only the exact successful POST triggers once',async()=>{
  let interventions=0,status=422;
  const wrapped=nativeVerificationBoundary(async()=>new Response('',{status}),endpoint,async()=>{interventions++;});
  await wrapped(endpoint,{method:'POST'});expect(interventions).toBe(0);
  status=200;
  await wrapped(new URL('/factors/other/verify',endpoint),{method:'POST'});
  await wrapped(new URL(endpoint.href+'?other=1'),{method:'POST'});
  await wrapped(new URL(endpoint.pathname,'http://unrelated.fixture'),{method:'POST'});
  await wrapped(endpoint,{method:'GET'});expect(interventions).toBe(0);
  await wrapped(endpoint,{method:'POST'});await wrapped(endpoint,{method:'POST'});expect(interventions).toBe(1);
 });
 test('an upstream failure never invokes intervention',async()=>{
  let interventions=0;
  const wrapped=nativeVerificationBoundary(async()=>{throw new Error('upstream refused');},endpoint,async()=>{interventions++;});
  await expect(wrapped(endpoint,{method:'POST'})).rejects.toThrow('upstream refused');expect(interventions).toBe(0);
 });
 test('failed intervention refuses response delivery and consumes the boundary',async()=>{
  let interventions=0;
  const wrapped=nativeVerificationBoundary(async()=>Response.json({access_token:'source-test-only'}),endpoint,async()=>{interventions++;throw new Error('revocation failed');});
  await expect(wrapped(endpoint,{method:'POST'})).rejects.toThrow('revocation failed');
  expect((await wrapped(endpoint,{method:'POST'})).ok).toBe(true);expect(interventions).toBe(1);
 });
});
