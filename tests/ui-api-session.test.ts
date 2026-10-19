import {expect,test} from 'bun:test';

/** A 401 from an expired access token must refresh and retry, not delete the server session. */
async function load() {
 (globalThis as any).location={origin:'https://console.test'};
 return await import('../ui/api');
}
function run(first:number[],refresh:'new'|'same'|'fail'){
 return async()=>{
  const {api,auth}=await load();
  const calls:string[]=[];let signedOut=0;
  const real=globalThis.fetch;
  globalThis.fetch=(async(_url:any,init:any)=>{calls.push(init.headers.authorization);
   const status=first[calls.length-1]??200;return Response.json({ok:true},{status});}) as any;
  (auth.auth as any).refreshSession=async()=>refresh==='fail'?{data:{session:null},error:new Error('refused')}:
   {data:{session:{access_token:refresh==='new'?'fresh':'stale'}},error:null};
  (auth.auth as any).signOut=async()=>{signedOut++;return {error:null};};
  try {
   const result=await api('stale')('/organizations').then(value=>({value}),error=>({error:(error as Error).message}));
   return {calls,signedOut,result};
  } finally {globalThis.fetch=real;}
 };
}

test('an expired token is refreshed and the request retried without signing out',async()=>{
 const {calls,signedOut,result}=await run([401],'new')();
 expect(calls).toEqual(['Bearer stale','Bearer fresh']);
 expect(signedOut).toBe(0);
 expect(result).toEqual({value:{ok:true}});
});

test('a refused refresh signs out',async()=>{
 const {calls,signedOut,result}=await run([401],'fail')();
 expect(calls).toEqual(['Bearer stale']);
 expect(signedOut).toBe(1);
 expect((result as any).error).toBe('Your session expired. Sign in again.');
});

test('a 401 that survives the refresh signs out and retries only once',async()=>{
 const {calls,signedOut}=await run([401,401],'new')();
 expect(calls).toEqual(['Bearer stale','Bearer fresh']);
 expect(signedOut).toBe(1);
});

test('a refresh that returns the same token does not loop',async()=>{
 const {calls,signedOut}=await run([401],'same')();
 expect(calls).toEqual(['Bearer stale']);
 expect(signedOut).toBe(1);
});
