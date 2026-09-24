// Fair share admission over real loopback HTTP: a controlled Bun upstream that holds every
// request until released, the gateway handler, and the application gate's policy (share 8,
// ceiling 24, 32 in total, 8 kept free). No Docker, no Supabase, no PostgreSQL.
// Usage: bun lab/fair-share-check.ts   Writes docs/evidence/fair-share-checks.json.
import {serveLocal} from '../src/http/local-server';
import {createGateway} from '../src/gateway/handler';
import {ConcurrencyGate} from '../src/gateway/concurrency';
import {PressureMonitor,type Saturation} from '../src/gateway/pressure';

const checks:string[]=[];
function check(name:string,ok:boolean){if(!ok)throw new Error(name);checks.push(name);}
let arrived=0;const waiting:(()=>void)[]=[];
const upstream=Bun.serve({hostname:'127.0.0.1',port:0,async fetch(){arrived++;await new Promise<void>(resolve=>waiting.push(resolve));return Response.json({ok:true});}});
const route={auth:`http://127.0.0.1:${upstream.port}`,rest:`http://127.0.0.1:${upstream.port}`,keys:['key'],anonymousToken:'anon',enabled:true};
const gate=new ConcurrencyGate(8,32,30_000,30_000,{ceiling:24,headroom:8});
const proxy=await serveLocal(createGateway(new Map(['shop','blog','docs'].map(env=>[env,route])),fetch,undefined,10_000,gate));
const url=(env:string)=>`http://127.0.0.1:${proxy.port}/${env}/rest/v1/`;
const call=(env:string)=>fetch(url(env),{headers:{apikey:'key'}});
async function until(n:number){const end=Date.now()+5000;while(arrived<n&&Date.now()<end)await Bun.sleep(5);return arrived>=n;}
const inflight:Promise<Response>[]=[];
async function drain(){while(waiting.length)waiting.shift()!();await Promise.all(inflight.splice(0).map(async r=>(await r).text()));arrived=0;}
const burst=async(env:string,n:number)=>{const start=arrived;const statuses:number[]=[];
 for(let i=0;i<n;i++){const response=call(env);const status=await Promise.race([response.then(r=>r.status),until(start+statuses.filter(s=>s===0).length+1).then(()=>0)]);
  if(status===0)inflight.push(response);else{statuses.push(status);await response.then(r=>r.text());continue;}statuses.push(0);}
 return {admitted:statuses.filter(s=>s===0).length,refused:statuses.filter(s=>s===429).length,full:statuses.filter(s=>s===503).length};};
try {
 const alone=await burst('shop',26);
 check('a busy environment alone borrows up to its ceiling of 24 over HTTP',alone.admitted===24&&alone.refused===2);
 const waking=await burst('blog',8);
 check('a quiet neighbour waking up gets its whole share at once from the room kept free',waking.admitted===8&&waking.refused===0&&waking.full===0);
 await drain();
 await burst('docs',1);await drain();   // docs has now asked recently, and so has blog
 const shared=await burst('shop',24);
 check('with two recently active neighbours the busy one borrows only up to 16',shared.admitted===16&&shared.refused===8);
 const b=await burst('blog',8),c=await burst('docs',8);
 check('both recent neighbours still get their whole share while it borrows',b.admitted===8&&c.admitted===8&&b.full+c.full===0);
 await drain();
 const reports:[string,Saturation][]=[];const monitor=new PressureMonitor(gate,(env,s)=>reports.push([env,s]),2);
 gate.pressure();
 for(let minute=0;minute<2;minute++){await burst('shop',25);monitor.sample();await drain();}
 check('two minutes refused at the ceiling produce one saturation notice',reports.length===1&&reports[0]![0]==='shop'&&reports[0]![1].minutes===2);
 await burst('shop',20);monitor.sample();monitor.sample();await drain();
 check('borrowing without refusals produces no notice',reports.length===1);
 gate.useShares(env=>env==='blog'?12:undefined);                     // the operator raised blog's share
 const busy=await burst('shop',24),raisedShare=await burst('blog',12);
 check('a raised share is reserved and served at once while another borrows',busy.admitted===12&&raisedShare.admitted===12&&raisedShare.full===0);
 await drain();gate.useShares(()=>undefined);
} finally {
 while(waiting.length)waiting.shift()!();
 proxy.stop(true);upstream.stop(true);
}
await Bun.write('docs/evidence/fair-share-checks.json',JSON.stringify({scope:'Real loopback HTTP through the gateway handler with a controlled Bun upstream that holds requests, using the application policy: share 8, ceiling 24, 32 in total, 8 kept free, neighbours seen in the last minute keep their unused share, and a share raised for one environment through useShares. Not Supabase or PostgreSQL, not sustained load, no Docker services started.',checks,count:checks.length},null,2)+'\n');
console.log(`${checks.length} fair share checks passed`);
