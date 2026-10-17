import {test,expect,afterAll} from 'bun:test';
import {run} from '../deploy/uptime-worker/worker.js';

// A local health route and a local stand-in for Telegram: nothing leaves the machine.
let healthy=true;const sent:any[]=[];
const health=Bun.serve({port:0,fetch:()=>healthy?Response.json({status:'ok',held:false}):new Response('down',{status:502})});
afterAll(()=>health.stop(true));
const realFetch=globalThis.fetch;
globalThis.fetch=(async(input:any,init?:any)=>{
 const url=String(input);
 if(url.startsWith('https://api.telegram.org/')){sent.push({url,body:JSON.parse(init.body)});return Response.json({ok:true});}
 return realFetch(input,init);
}) as typeof fetch;
afterAll(()=>{globalThis.fetch=realFetch;});
function env(token=true){const store=new Map<string,string>();
 return {HEALTH_URL:`http://127.0.0.1:${health.port}/health`,CHAT_ID:'4242',...(token?{TELEGRAM_TOKEN:'123:secret'}:{}),
  STATE:{get:async(key:string)=>store.get(key)??null,put:async(key:string,value:string)=>{store.set(key,value);}}};}

test('two failures in a row send one message, recovery sends one, nothing repeats',async()=>{
 const e=env();sent.length=0;
 healthy=true;expect((await run(e,0)).down).toBe(false);
 healthy=false;expect((await run(e,300_000)).down).toBe(false);   // one failure is not an outage
 expect(sent).toEqual([]);
 expect((await run(e,600_000)).down).toBe(true);
 await run(e,900_000);await run(e,1_200_000);
 expect(sent.length).toBe(1);
 expect(sent[0].body.chat_id).toBe('4242');expect(sent[0].body.text).toContain('down');
 expect(sent[0].body.text).not.toContain('secret');
 healthy=true;const back=await run(e,1_500_000);
 expect(back.down).toBe(false);expect(sent.length).toBe(2);expect(sent[1].body.text).toContain('back');expect(sent[1].body.text).toContain('15 minutes');
 await run(e,1_800_000);expect(sent.length).toBe(2);
});

test('without a token the state is kept and nothing is sent',async()=>{
 const e=env(false);sent.length=0;healthy=false;
 await run(e,0);expect((await run(e,1)).down).toBe(true);
 expect(sent).toEqual([]);
});
