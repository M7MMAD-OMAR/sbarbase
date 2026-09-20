/** Local operator protocol on stdin, never an HTTP endpoint. Caller holds operation.lock. */
import {Catalog} from '../src/control/catalog';
const catalog=new Catalog('.lab/upstream/control.sqlite');
try {
 const input=await Bun.stdin.json();
 if(!Array.isArray(input.runtimes)||!input.runtimes.every((r:unknown)=>typeof r==='string'&&/^e_[a-f0-9]{24}$/.test(r)))throw new Error('Invalid runtimes');
 if(input.action==='read')console.log(JSON.stringify(Object.fromEntries(input.runtimes.map((r:string)=>[r,catalog.runtimeRouting(r)]))));
 else if(input.action==='pause') {
  if(input.runtimes.length!==1||!Number.isSafeInteger(input.revision))throw new Error('Invalid pause');
  console.log(JSON.stringify({revision:catalog.changeRuntimeRouting(input.runtimes[0],input.revision,'pause')}));
 }else throw new Error('Unsupported operator action');
} catch {console.error('Routing operator refused request');process.exitCode=1;}
finally {catalog.close();}
