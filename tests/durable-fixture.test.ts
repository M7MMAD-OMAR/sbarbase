import {describe,expect,test} from 'bun:test';
import {mkdtempSync,readFileSync,readdirSync,writeFileSync,statSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {selectFixture,writeFixture,type Candidate} from '../lab/durable-fixture';

const A='e_'+'a'.repeat(24),B='e_'+'b'.repeat(24),MOVED='e_'+'c'.repeat(24);
const ORG='org-1',PROJECT='project-1';
const candidates:Candidate[]=[
  {environment:'env-a',project:PROJECT,organization:ORG,runtime:A,state:'succeeded'},
  {environment:'env-b',project:'project-2',organization:ORG,runtime:B,state:'succeeded'},
  {environment:'env-c',project:PROJECT,organization:ORG,runtime:MOVED,state:'succeeded'},
  {environment:'env-d',project:PROJECT,organization:ORG,runtime:null,state:'failed'},
];
const served=()=>({published:true,maintenance:false,fenced:false});

describe('durable fixture selection',()=>{
  test('two served runtimes resolve to their environments in order',()=>{
    expect(selectFixture(candidates,[A,B],[MOVED],served,PROJECT))
      .toEqual({organization:ORG,project:PROJECT,environments:['env-a','env-b'],runtimes:[A,B]});
  });
  test('an excluded runtime is refused before any observation',()=>{
    let observed=0;
    expect(()=>selectFixture(candidates,[A,MOVED],[MOVED],()=>{observed++;return served();},PROJECT)).toThrow('excluded');
    expect(observed).toBe(1);
  });
  test('unpublished, paused or fenced runtimes are refused clearly',()=>{
    expect(()=>selectFixture(candidates,[A,B],[],r=>({...served(),published:r!==B}),PROJECT)).toThrow('not published');
    expect(()=>selectFixture(candidates,[A,B],[],r=>({...served(),maintenance:r===B}),PROJECT)).toThrow('paused in routing');
    expect(()=>selectFixture(candidates,[A,B],[],r=>({...served(),fenced:r===A}),PROJECT)).toThrow('fenced');
  });
  test('an unknown, duplicated or unprovisioned runtime is refused',()=>{
    const other='e_'+'d'.repeat(24);
    expect(()=>selectFixture(candidates,[A,other],[],served,PROJECT)).toThrow('0 catalog environments');
    expect(()=>selectFixture([...candidates,{...candidates[0]!,environment:'env-x'}],[A,B],[],served,PROJECT)).toThrow('2 catalog environments');
    expect(()=>selectFixture(candidates.map(c=>c.runtime===B?{...c,state:'failed'}:c),[A,B],[],served,PROJECT)).toThrow('not provisioned');
    expect(()=>selectFixture(candidates,[A,A],[],served,PROJECT)).toThrow('two distinct');
    expect(()=>selectFixture(candidates,[A],[],served,PROJECT)).toThrow('two distinct');
  });
  test('runtimes in different organizations or a foreign project are refused',()=>{
    expect(()=>selectFixture(candidates.map(c=>c.runtime===B?{...c,organization:'org-2'}:c),[A,B],[],served,PROJECT)).toThrow('different organizations');
    expect(()=>selectFixture(candidates,[A,B],[],served,'project-9')).toThrow('fixture project');
  });
});

describe('durable fixture file',()=>{
  const fixture={organization:ORG,project:PROJECT,environments:['env-a','env-b'],runtimes:[A,B]};
  test('a different earlier fixture is archived byte for byte, then replaced',()=>{
    const directory=mkdtempSync(join(tmpdir(),'fixture-'));
    writeFileSync(join(directory,'probe.json'),'{"old":true}');
    expect(writeFixture(directory,fixture,'20260925').archived).toBe(join(directory,'probe.pre-20260925.json'));
    expect(readFileSync(join(directory,'probe.pre-20260925.json'),'utf8')).toBe('{"old":true}');
    expect(JSON.parse(readFileSync(join(directory,'probe.json'),'utf8'))).toEqual(fixture);
    expect(statSync(join(directory,'probe.json')).mode&0o777).toBe(0o600);
  });
  test('rewriting the same fixture archives nothing, and an archive is never overwritten',()=>{
    const directory=mkdtempSync(join(tmpdir(),'fixture-'));
    writeFileSync(join(directory,'probe.json'),'{"old":1}');
    writeFileSync(join(directory,'probe.pre-20260925.json'),'{"older":0}');
    expect(writeFixture(directory,fixture,'20260925').archived).toBe(join(directory,'probe.pre-20260925-1.json'));
    expect(readFileSync(join(directory,'probe.pre-20260925.json'),'utf8')).toBe('{"older":0}');
    expect(writeFixture(directory,fixture,'20260925').archived).toBeNull();
    expect(readdirSync(directory).sort()).toEqual(['probe.json','probe.pre-20260925-1.json','probe.pre-20260925.json']);
  });
  test('a first fixture needs no archive',()=>{
    const directory=mkdtempSync(join(tmpdir(),'fixture-'));
    expect(writeFixture(directory,fixture,'20260925').archived).toBeNull();
  });
});
