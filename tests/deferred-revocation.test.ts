import {expect,test} from 'bun:test';
import {existsSync,mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {functionsHandler} from '../src/control/functions';
import {signInHandler} from '../src/control/sign-in';
import {databaseHandler} from '../src/control/database';

function fixture() {
 const catalog=new Catalog(':memory:'),root=mkdtempSync(join(tmpdir(),'revocation-'));
 const organization=catalog.createOrganization('owner','Organization');
 catalog.setMember('owner',organization,'admin','admin');
 const environment=catalog.createEnvironment('owner',catalog.createProject('owner',organization,'Project'),'production');
 const job=catalog.claimProvision()!;catalog.finishProvision(environment,job.claim!,true);
 return {catalog,root,organization,environment,runtime:job.runtime,
  close(){catalog.close();rmSync(root,{recursive:true,force:true});}};
}

const identify=async(request:Request)=>request.headers.get('authorization');
function request(environment:string,path:string,input:unknown) {
 return new Request(`http://local/management/v1/environments/${environment}/${path}`,{
  method:'PUT',headers:{authorization:'admin','content-type':'application/json'},body:JSON.stringify(input)});
}

async function revokeWhileReading(handler:(request:Request)=>Promise<Response>,environment:string,path:string,
 input:unknown,revoke:()=>void) {
 let controller!:ReadableStreamDefaultController<Uint8Array>;
 const body=new ReadableStream<Uint8Array>({start(value){controller=value;}});
 const incoming=new Request(`http://local/management/v1/environments/${environment}/${path}`,{
  method:'PUT',headers:{authorization:'admin','content-type':'application/json'},body,duplex:'half'} as RequestInit);
 const response=handler(incoming);
 for(let turn=0;turn<100&&!incoming.body!.locked;turn++)await Promise.resolve();
 expect(incoming.body!.locked).toBe(true);
 revoke();controller.enqueue(new TextEncoder().encode(JSON.stringify(input)));controller.close();
 return response;
}

for(const role of [null,'viewer'] as const) {
 const change=role===null?'removed':'demoted';
 test(`function secrets stay intact when an admin is ${change} while reading the body`,async()=>{
  const f=fixture();
  try {
   const handler=functionsHandler(f.catalog,identify,join(f.root,'code'),join(f.root,'secrets'));
   expect((await handler(request(f.environment,'function-secrets',{secrets:{DUMMY:'before'}}))).status).toBe(200);
   const path=join(f.root,'secrets',f.runtime,'secrets.json'),before=readFileSync(path,'utf8');
   const response=await revokeWhileReading(handler,f.environment,'function-secrets',{secrets:{DUMMY:'after'}},
    ()=>f.catalog.changeMember('owner',f.organization,'admin',role));
   expect(response.status).toBe(403);expect(readFileSync(path,'utf8')).toBe(before);
  } finally {f.close();}
 });
 test(`a function is not published when an admin is ${change} while reading the body`,async()=>{
  const f=fixture();
  try {
   const handler=functionsHandler(f.catalog,identify,join(f.root,'code'),join(f.root,'secrets'));
   const incoming=new Request(`http://local/management/v1/environments/${f.environment}/functions`,{headers:{authorization:'admin'}});
   expect((await handler(incoming)).status).toBe(200);
   const response=await revokeWhileReading(handler,f.environment,'functions/example',{files:{'index.ts':'export const value=1;'}},
    ()=>f.catalog.changeMember('owner',f.organization,'admin',role));
   expect(response.status).toBe(403);expect(existsSync(join(f.root,'code',f.runtime,'functions.json'))).toBe(false);
   expect(f.catalog.functions('owner',f.environment).desired).toBe('off');
  } finally {f.close();}
 });
 test(`sign-in settings stay intact when an admin is ${change} while reading the body`,async()=>{
  const f=fixture();
  try {
   const handler=signInHandler(f.catalog,identify,f.root);
   expect((await handler(request(f.environment,'sign-in',{site_url:'https://before.example'}))).status).toBe(202);
   const path=join(f.root,f.runtime+'-auth.json'),before=readFileSync(path,'utf8');
   const response=await revokeWhileReading(handler,f.environment,'sign-in',{site_url:'https://after.example'},
    ()=>f.catalog.changeMember('owner',f.organization,'admin',role));
   expect(response.status).toBe(403);expect(readFileSync(path,'utf8')).toBe(before);
   expect(f.catalog.signIn('owner',f.environment).revision).toBe(1);
  } finally {f.close();}
 });
 test(`no database password is written when an admin is ${change} while reading the body`,async()=>{
  const f=fixture();
  try {
   const handler=databaseHandler(f.catalog,identify,f.root);
   const incoming=new Request(`http://local/management/v1/environments/${f.environment}/database`,{headers:{authorization:'admin'}});
   expect((await handler(incoming)).status).toBe(200);
   const response=await revokeWhileReading(handler,f.environment,'database',{enabled:true},
    ()=>f.catalog.changeMember('owner',f.organization,'admin',role));
   expect(response.status).toBe(403);expect(existsSync(join(f.root,f.runtime+'.json'))).toBe(false);
   expect(f.catalog.databaseAccess('owner',f.environment).desired).toBe('off');
  } finally {f.close();}
 });
}
