// A console process for the handover tests: lab/upstream-server.ts's gateway and signal wiring over a stand-in
// application, on loopback ports the test chooses. ROLE=serving|candidate, STATE (the folder for
// the records), PORT (the console), DATABASE_PORT, STUDIO_PORT, TAG (printed in every answer).
import {renameSync,unlinkSync,writeFileSync} from 'node:fs';
import {startGateway,type GatewayApplication} from '../../lab/upstream-server';
import {drainDeadline,stopOnSignals} from '../../src/http/local-server';
import {DirectDatabase} from '../../src/http/database-proxy';
import {candidateSignals,ListenerRecord,PROMOTION_FAILED,publishPromoted} from '../../src/http/handover';

/** A file the tests and the supervisor read whole: written aside and renamed. */
const publish=(path:string,value:unknown) => {writeFileSync(path+'.pending',JSON.stringify(value));renameSync(path+'.pending',path);};
const state=process.env.STATE!,candidate=process.env.ROLE==='candidate',protocol=1;
const app:GatewayApplication={
 handler:async()=>new Response(process.env.TAG??'x'),studio:async()=>new Response('studio'),upstream:async()=>new Response('upstream'),
 realtime:()=>({ok:false,status:404,message:'none'}),database:new DirectDatabase(),listening(){},
 catalog:{schemaVersion:()=>1,runtimeReady:()=>true,externalDatabaseEnabled:()=>false},keys:{check(){},confirmationProbe:undefined as never},
};
let promote:()=>Promise<void>=async()=>{},release=()=>{};
if(candidate){
 const signalled=candidateSignals({promote:()=>promote(),release:()=>release(),
  failed:error=>{console.error(`Promotion failed: ${(error as Error).message}`);process.exit(PROMOTION_FAILED);}});
 process.on('SIGUSR2',signalled);
}
const gateway=await startGateway(app,{hold:()=>undefined,consolePort:Number(process.env.PORT),
 databaseAt:process.env.DATABASE_PORT?{port:Number(process.env.DATABASE_PORT),host:'127.0.0.1'}:null,
 endpoints:()=>({}),database:()=>undefined,
 studio:()=>process.env.STUDIO_PORT?{host:'127.0.0.1',port:Number(process.env.STUDIO_PORT)}:undefined,
 handover:{candidate,protocol,stateDirectory:state,freezeEveryMs:100}});
if(candidate){
 publish(`${state}/candidate.json`,{url:`http://127.0.0.1:${gateway.port}`,pid:process.pid,protocol});
 promote=async()=>{
  const at=await gateway.promote!();
  publishPromoted(state,at,`http://127.0.0.1:${gateway.port}`,protocol);
 };
 release=()=>gateway.release!();
}else publish(`${state}/server.json`,{url:`http://127.0.0.1:${gateway.port}`,pid:process.pid,protocol});
stopOnSignals(async()=>{await gateway.drain(drainDeadline(process.env.SBARBASE_DRAIN_MS));},()=>{
 gateway.stop();
 new ListenerRecord(state,process.pid,protocol,candidate?'candidate':'serving').remove();
 try{unlinkSync(`${state}/candidate.json`);}catch{}
 process.exit(0);
});
// Only now is a stop answered gracefully, so the tests wait for this line before they signal the process.
console.log('READY');
