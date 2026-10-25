import {mkdirSync,mkdtempSync,readFileSync,renameSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createServer} from 'node:net';

/** Ports for tests that run real listeners, below Linux's ephemeral range (32768 to 60999) where clients take their
 * source ports. A port is reserved for the life of the test by creating a folder named after it (an atomic
 * claim, shared with lab/test_handover.py, which uses the same folder), so two test runs at once never pick the
 * same one. A claim whose owner process is gone is taken over. */
const claims=join(tmpdir(),'sbarbase-handover-ports');
const mine:string[]=[];
const alive=(pid:number) => {try{process.kill(pid,0);return true;}catch(error){return (error as NodeJS.ErrnoException).code==='EPERM';}};
export async function reservePort():Promise<number> {
 mkdirSync(claims,{recursive:true});
 for(;;){
  const port=21000+Math.floor(Math.random()*10000),folder=join(claims,String(port));
  // The claim appears whole or not at all: a folder with its owner inside is renamed into place, which fails while
  // the name is taken, so no other run sees it without an owner and takes it for dead.
  const made=mkdtempSync(join(claims,'claim-'));
  writeFileSync(join(made,'owner'),String(process.pid));
  let claimed=false;
  try{renameSync(made,folder);claimed=true;}
  catch{
   rmSync(made,{recursive:true,force:true});
   let owner=0;try{owner=Number(readFileSync(join(folder,'owner'),'utf8'));}catch{}
   // A claim whose owner is gone is removed (by renaming it away first, so two runs never both take it over).
   if(!owner||alive(owner))continue;
   const away=join(claims,`stale-${process.pid}-${port}-${Date.now()}`);
   try{renameSync(folder,away);}catch{continue;}
   rmSync(away,{recursive:true,force:true});
   continue;
  }
  if(!claimed)continue;
  const probe=createServer();
  const free=await new Promise<boolean>(resolve => {probe.once('error',() => resolve(false));probe.listen({port,host:'127.0.0.1'},() => resolve(true));});
  if(free)await new Promise<void>(resolve => probe.close(() => resolve()));
  if(!free){rmSync(folder,{recursive:true,force:true});continue;}
  mine.push(folder);
  return port;
 }
}
export function releasePorts() {
 for(const folder of mine.splice(0))rmSync(folder,{recursive:true,force:true});
}
