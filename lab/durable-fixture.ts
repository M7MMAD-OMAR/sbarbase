// The retained durable lifecycle fixture: which environments lab/durable-check.ts may use,
// and how its probe.json is replaced. Pure selection plus one archive-then-write; the probe
// gathers the observations (catalog, endpoints, routing, fence state) and passes them in.
import {closeSync,existsSync,fsyncSync,openSync,readFileSync,renameSync,writeSync} from 'node:fs';
import {dirname,join} from 'node:path';

const RUNTIME=/^e_[a-f0-9]{24}$/;

export type Candidate={environment:string;project:string;organization:string;runtime:string|null;state:string|null};
export type Observation={published:boolean;maintenance:boolean;fenced:boolean};
export type Fixture={organization:string;project:string;environments:string[];runtimes:string[]};

/** Resolve each named runtime to its one environment, refusing anything that is not served here. */
export function selectFixture(candidates:readonly Candidate[],runtimes:readonly string[],excluded:readonly string[],
                              observe:(runtime:string)=>Observation,project:string):Fixture {
  if(runtimes.length!==2||new Set(runtimes).size!==2)throw new Error('The durable fixture names exactly two distinct runtimes');
  const environments:string[]=[];let organization:string|undefined;
  for(const runtime of runtimes) {
    if(!RUNTIME.test(runtime))throw new Error('Invalid fixture runtime identifier');
    if(excluded.includes(runtime))throw new Error(`Fixture runtime ${runtime} is excluded from this probe`);
    const matches=candidates.filter(item=>item.runtime===runtime);
    if(matches.length!==1)throw new Error(`Fixture runtime ${runtime} has ${matches.length} catalog environments, expected one`);
    const match=matches[0]!;
    if(match.state!=='succeeded')throw new Error(`Fixture runtime ${runtime} is not provisioned`);
    if(organization!==undefined&&match.organization!==organization)throw new Error('Fixture runtimes belong to different organizations');
    organization=match.organization;
    const seen=observe(runtime);
    if(!seen.published)throw new Error(`Fixture runtime ${runtime} is not published`);
    if(seen.maintenance)throw new Error(`Fixture runtime ${runtime} is paused in routing`);
    if(seen.fenced)throw new Error(`Fixture runtime ${runtime} is fenced on the source`);
    environments.push(match.environment);
  }
  if(!candidates.some(item=>item.project===project&&item.organization===organization))
    throw new Error('The fixture project does not belong to the fixture organization');
  return {organization:organization!,project,environments,runtimes:[...runtimes]};
}

function durableWrite(path:string,text:string,exclusive:boolean) {
  const descriptor=openSync(path,exclusive?'wx':'w',0o600);
  try{writeSync(descriptor,text);fsyncSync(descriptor);}finally{closeSync(descriptor);}
  const directory=openSync(dirname(path),'r');
  try{fsyncSync(directory);}finally{closeSync(directory);}
}

/** Write probe.json. A different earlier fixture is archived beside it first and never overwritten. */
export function writeFixture(directory:string,fixture:Fixture,day:string):{archived:string|null} {
  if(!/^\d{8}$/.test(day))throw new Error('Archive day must be YYYYMMDD');
  const path=join(directory,'probe.json'),text=JSON.stringify(fixture);
  let archived:string|null=null;
  if(existsSync(path)) {
    const current=readFileSync(path,'utf8');
    if(current===text)return {archived:null};
    for(let index=0;;index++) {
      const candidate=join(directory,`probe.pre-${day}${index?'-'+index:''}.json`);
      if(existsSync(candidate)) {
        if(readFileSync(candidate,'utf8')===current){archived=candidate;break;}
        continue;
      }
      durableWrite(candidate,current,true);archived=candidate;break;
    }
  }
  const temporary=path+'.tmp';
  durableWrite(temporary,text,false);
  renameSync(temporary,path);
  const handle=openSync(directory,'r');
  try{fsyncSync(handle);}finally{closeSync(handle);}
  return {archived};
}
