export const factorId='11111111-1111-4111-8111-111111111111';
export const otherFactorId='22222222-2222-4222-8222-222222222222';
export const sessionId='33333333-3333-4333-8333-333333333333';
export const challengeId='44444444-4444-4444-8444-444444444444';
export const verifiedFactor={id:factorId,factor_type:'totp',status:'verified'};
/** A fake signature is accepted only by the test's explicit Auth transport fixture. */
export function managementToken(actor='owner',aal='aal2',session=sessionId,now=Math.floor(Date.now()/1000),extra:Record<string,unknown>={}){
 return 'eyJhbGciOiJIUzI1NiJ9.'+Buffer.from(JSON.stringify({sub:actor,session_id:session,exp:now+3600,aal,
  amr:[{method:'password',timestamp:now-5},...(aal==='aal2'?[{method:'totp',timestamp:now}]:[])],...extra})).toString('base64url')+'.fixture';
}
