import {test,expect,beforeEach,afterEach} from 'bun:test';
import {mkdtempSync,readFileSync,readdirSync,rmSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {Catalog} from '../src/control/catalog';
import {managementHandler} from '../src/control/http';
import {DEFAULT_SETTINGS} from '../src/control/updates';

let catalog:Catalog,installation:string,directory:string,handler:ReturnType<typeof managementHandler>;
beforeEach(()=>{
  directory=mkdtempSync(join(tmpdir(),'sbarbase-operator-revocation-'));
  catalog=new Catalog(':memory:');
  installation=catalog.initializeInstallation('fixture-bootstrap','owner','Installation');
  catalog.setMember('owner',installation,'operator','admin');
  catalog.setMember('owner',installation,'neighbor','admin');
  writeFileSync(join(directory,'settings.json'),JSON.stringify(DEFAULT_SETTINGS));
  writeFileSync(join(directory,'current.json'),JSON.stringify({
    apply:{version:'0.2.0',tag:'v0.2.0',possible:true,reason:null,acknowledgement:false},
  }));
  handler=managementHandler(catalog,async request=>request.headers.get('authorization'),'.',undefined,directory);
});
afterEach(()=>{catalog.close();rmSync(directory,{recursive:true,force:true});});

const routes=[
  {name:'organization creation',path:'/organizations',method:'POST',input:{name:'New client'},status:201},
  {name:'update settings',path:'/updates/settings',method:'PUT',
    input:{check:false,automatic:false,window:{start:'02:00',end:'03:00'}},status:200},
  {name:'update apply',path:'/updates/apply',method:'POST',input:{version:'0.2.0'},status:202},
];

function files() {
  return Object.fromEntries(readdirSync(directory).sort().map(name=>[name,readFileSync(join(directory,name),'utf8')]));
}

function pausedRequest(route:typeof routes[number],actor='operator') {
  let controller:ReadableStreamDefaultController<Uint8Array>;
  let reading:()=>void=()=>{};
  let reads=0;
  const started=new Promise<void>(resolve=>{reading=resolve;});
  const stream=new ReadableStream<Uint8Array>({
    start(value){controller=value;},
    pull(){reads++;reading();},
  },{highWaterMark:0});
  const request=new Request('http://local/management/v1'+route.path,{
    method:route.method,headers:{authorization:actor,'content-type':'application/json'},body:stream,
  });
  return {request,started,reads:()=>reads,finish(){
    controller.enqueue(new TextEncoder().encode(JSON.stringify(route.input)));controller.close();
  }};
}

async function neighborRequest(route:typeof routes[number]) {
  return handler(new Request('http://local/management/v1'+route.path,{
    method:route.method,headers:{authorization:'neighbor','content-type':'application/json'},body:JSON.stringify(route.input),
  }));
}

for(const route of routes) {
  for(const role of ['viewer',null] as const) {
    test(`${route.name} refuses an operator ${role===null?'removed':'demoted'} while reading its body`,async()=>{
      expect(catalog.installationOperator('operator')).toBe(true);
      const paused=pausedRequest(route),pending=handler(paused.request);
      await paused.started;
      catalog.changeMember('owner',installation,'operator',role);
      expect(catalog.installationOperator('operator')).toBe(false);
      const organizations=catalog.listOrganizations('operator'),before=files();
      paused.finish();
      const response=await pending;
      expect(response.status).toBe(403);
      expect(await response.json()).toEqual({message:'Forbidden'});
      expect(catalog.listOrganizations('operator')).toEqual(organizations);
      expect(files()).toEqual(before);
      expect(catalog.installationOperator('neighbor')).toBe(true);
      expect((await neighborRequest(route)).status).toBe(route.status);
    });
  }

  test(`${route.name} still accepts an operator after a delayed valid body`,async()=>{
    const paused=pausedRequest(route),pending=handler(paused.request);
    await paused.started;
    paused.finish();
    expect((await pending).status).toBe(route.status);
    if(route.path==='/organizations')
      expect(catalog.listOrganizations('operator').some(item=>item.name===route.input.name)).toBe(true);
    else if(route.path==='/updates/settings')
      expect(JSON.parse(readFileSync(join(directory,'settings.json'),'utf8'))).toEqual(route.input);
    else {
      const requested=JSON.parse(readFileSync(join(directory,'request.json'),'utf8'));
      expect(requested.kind).toBe('apply');expect(requested.version).toBe('0.2.0');
    }
  });

  test(`${route.name} refuses an already demoted operator before reading its body`,async()=>{
    catalog.changeMember('owner',installation,'operator','viewer');
    const before=files(),paused=pausedRequest(route);
    const response=await handler(paused.request);
    expect(response.status).toBe(403);
    expect(paused.reads()).toBe(0);
    expect(files()).toEqual(before);
    await paused.request.body?.cancel();
  });
}
