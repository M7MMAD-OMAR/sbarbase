import {Catalog} from '../src/control/catalog';
import {managementHandler} from '../src/control/http';
import {managementIdentity} from '../src/control/auth';

// In this probe only, a_stage represents the management identity realm.
// Production must provision a dedicated management realm, not reuse app staging.
const endpoints=await Bun.file('.lab/endpoints.json').json() as Record<string,{auth:string}>;
const control=endpoints.a_stage,application=endpoints.a_prod;
if(!control||!application) throw new Error('Start component lab first');
const checks:{check:string;passed:boolean}[]=[];
function check(name:string,passed:boolean) {
 checks.push({check:name,passed});if(!passed) throw new Error(name);
}
async function signup(endpoint:string,tag:string) {
 const suffix=crypto.randomUUID();
 const response=await fetch(`${endpoint}/signup`,{method:'POST',headers:{'content-type':'application/json'},
  body:JSON.stringify({email:`management-${tag}-${suffix}@example.com`,password:`Local-${suffix}`}),signal:AbortSignal.timeout(5000)});
 const data=await response.json();
 if(!response.ok||!data.access_token||!data.user?.id) throw new Error('Lab signup failed');
 return {token:data.access_token as string,id:data.user.id as string};
}
const catalog=new Catalog(':memory:');
const transport=(async(input,init)=>{
 const url=new URL(String(input));
 if(url.origin!=='http://management.invalid'||url.pathname!=='/auth/v1/user') throw new Error('Unexpected Auth request');
 return fetch(`${control.auth}/user`,init);
}) as typeof fetch;
const handler=managementHandler(catalog,managementIdentity('http://management.invalid','lab-only-key',transport));
const server=Bun.serve({hostname:'127.0.0.1',port:0,fetch:handler});
try {
 const owner=await signup(control.auth,'owner'),viewer=await signup(control.auth,'viewer');
 const outsider=await signup(control.auth,'outsider'),app=await signup(application.auth,'application');
 const organization=catalog.createOrganization(owner.id,'Live management probe');
 catalog.setMember(owner.id,organization,viewer.id,'viewer');
 const path=`/management/v1/organizations/${organization}/projects`;
 async function request(token:string,route=path,method='GET',payload?:unknown) {
  return fetch(`http://127.0.0.1:${server.port}${route}`,{method,headers:{authorization:`Bearer ${token}`,
   'content-type':'application/json','x-user-id':owner.id},
   ...(payload===undefined?{}:{body:JSON.stringify(payload)}),signal:AbortSignal.timeout(10000)});
 }
 check('application realm token rejected by live management Auth',(await request(app.token)).status===401);
 check('tampered token rejected',(await request(owner.token.slice(0,-8)+'tampered')).status===401);
 check('valid nonmember denied despite injected owner header',(await request(outsider.token)).status===403);
 check('viewer cannot create project',(await request(viewer.token,path,'POST',{name:'Denied'})).status===403);
 check('body actor spoofing rejected',(await request(owner.token,path,'POST',{name:'Denied',actor:outsider.id})).status===400);
 const created=await request(owner.token,path,'POST',{name:'Live project'});
 check('owner creates project metadata',created.status===201);
 const project=await created.json();
 const envPath=`/management/v1/projects/${project.id}/environments`;
 check('owner creates production metadata',(await request(owner.token,envPath,'POST',{name:'production'})).status===201);
 check('viewer reads project environments',(await request(viewer.token,envPath)).status===200);
 catalog.setMember(owner.id,organization,viewer.id,null);
 check('membership revocation denies same unexpired token',(await request(viewer.token,envPath)).status===403);
 const listing=await (await request(owner.token)).json();
 check('denied writes created no projects',listing.data.length===1);
 console.log(`${checks.length} live management checks passed.`);
} finally {
 server.stop(true);catalog.close();
 await Bun.write('.lab/management-verification.json',JSON.stringify({scope:'Live Auth/HTTP authorization using a_stage as a temporary management realm; metadata only, no dedicated management deployment',checks},null,2));
}
