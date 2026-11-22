import {expect,test} from 'bun:test';
import {mkdtempSync,mkdirSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {STUDIO_STEPS,studioProgress} from '../src/control/studio-progress';

const runtime='e_'+'a'.repeat(24);
function withFile(content:string,run:(directory:string)=>void){
  const directory=mkdtempSync(join(tmpdir(),'studio-progress-'));
  try{mkdirSync(directory,{recursive:true});writeFileSync(join(directory,runtime+'.json'),content);run(directory);}finally{rmSync(directory,{recursive:true,force:true});}
}

test('reports the step and the steps already begun, in order',()=>{
  withFile(JSON.stringify({started:1000,step:'meta',steps:[{id:'meta',at:5},{id:'checks',at:1},{id:'studio',at:3}]}),directory=>{
    expect(studioProgress(runtime,directory)).toEqual({step:'meta',startedAt:1000,steps:['checks','studio','meta']});
  });
});

test('ignores a missing, malformed or unknown report',()=>{
  expect(studioProgress(runtime,'/nonexistent')).toBeNull();
  withFile('not json',directory=>expect(studioProgress(runtime,directory)).toBeNull());
  withFile(JSON.stringify({started:1,step:'launch-rockets',steps:[]}),directory=>expect(studioProgress(runtime,directory)).toBeNull());
  withFile(JSON.stringify({started:'now',step:'meta',steps:[]}),directory=>expect(studioProgress(runtime,directory)).toBeNull());
  withFile('x'.repeat(5000),directory=>expect(studioProgress(runtime,directory)).toBeNull());
});

test('never reads a path built from anything but a runtime id',()=>{
  expect(studioProgress('../../etc/passwd')).toBeNull();
  expect(studioProgress('e_'+'A'.repeat(24))).toBeNull();
});

test('names the same steps as the worker',async()=>{
  const source=await Bun.file(join(import.meta.dir,'..','lab','studio.py')).text();
  const listed=/STEPS = \(([^)]*)\)/.exec(source)![1]!.split(',').map(item=>item.trim().replace(/'/g,'')).filter(Boolean);
  expect(listed).toEqual([...STUDIO_STEPS]);
});
