import {mkdtempSync,rmSync,readFileSync,statSync} from 'node:fs';
import {createClient} from '@supabase/supabase-js';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {bootstrapOperator} from '../src/control/bootstrap';
import {bootstrapJournal} from './bootstrap-journal';
import {bootstrapAuth} from './bootstrap-auth';
import {internalToken,managementPublishableKey} from './upstream-app';

const checks:{check:string;passed:boolean}[]=[];
function check(name:string,passed:boolean){checks.push({check:name,passed});if(!passed)throw new Error(name);}
const directory=mkdtempSync('.secrets/upstream/bootstrap-check-');
let catalog=new Catalog(directory+'/control.sqlite');
const keys=new KeyStore(directory+'/keys.sqlite'),journal=bootstrapJournal(directory+'/intent.json');
const management=await Bun.file('.lab/upstream/management.json').json();
const values=await Bun.file('.secrets/upstream/runtime.json').json();
const auth=bootstrapAuth(management.auth,values.management.jwt);
const suffix=crypto.randomUUID(),input={email:`bootstrap-${suffix}@example.com`,password:`Local-${suffix}`,organization:'Bootstrap probe'};
let identity:string|undefined,server:ReturnType<typeof Bun.serve>|undefined;
try {
 let crashed=false;
 try{await bootstrapOperator(catalog,auth,journal,input,phase=>{if(phase==='identity-created')throw new Error('Simulated interruption');});}
 catch(error){if((error as Error).message==='Simulated interruption')crashed=true;else throw error;}
 check('interruption after actual Auth user creation exercised',crashed);
 check('catalog has no authority before setup commit',catalog.installationBootstrap()===null);
 const created=await auth.find(journal.read()!.operation);identity=created?.id;
 check('operation marker locates identity despite missing local actor ID',!!identity&&!journal.read()!.actor);
 let wrongPassword=false;
 try{await bootstrapOperator(catalog,auth,journal,{...input,password:'different-password-for-probe'});}catch{wrongPassword=true;}
 check('recovery refuses a different password without granting ownership',wrongPassword&&catalog.installationBootstrap()===null);
 catalog.close();catalog=new Catalog(directory+'/control.sqlite');
 let commitInterrupted=false;
 try{await bootstrapOperator(catalog,auth,journal,input,phase=>{if(phase==='catalog')throw new Error('Simulated commit interruption');});}
 catch(error){if((error as Error).message==='Simulated commit interruption')commitInterrupted=true;else throw error;}
 check('interruption after actual catalog commit exercised',commitInterrupted);
 catalog.close();catalog=new Catalog(directory+'/control.sqlite');
 const recovered=await bootstrapOperator(catalog,auth,journal,input);
 check('recovery reuses original Auth identity',recovered.actor===identity);
 check('recovery preserves one organization',catalog.listOrganizations(recovered.actor).length===1);
 check('bootstrap journal excludes password',!readFileSync(directory+'/intent.json','utf8').includes(input.password));
 check('bootstrap journal permissions are private',(statSync(directory+'/intent.json').mode&0o777)===0o600);
 const repeat=await bootstrapOperator(catalog,auth,journal,input);
 check('completed setup retry is idempotent',repeat.actor===recovered.actor&&repeat.organization===recovered.organization);
 let missingRejected=false;
 try{await bootstrapOperator(catalog,auth,{read:()=>null,write(){throw new Error('Should not write');}},input);}catch{missingRejected=true;}
 check('missing journal cannot initialize another owner',missingRejected);
 const fetchHandler=application(catalog,keys,{auth:management.auth,publishableKey:managementPublishableKey,anonymousToken:internalToken(values.management.jwt,'anon')},()=>undefined);
 server=Bun.serve({hostname:'127.0.0.1',port:0,fetch:fetchHandler});const base=`http://127.0.0.1:${server.port}`;
 const sdk=createClient(base+'/management',managementPublishableKey,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false}});
 const login=await sdk.auth.signInWithPassword({email:input.email,password:input.password});
 check('bootstrapped operator logs in through composed API',!login.error&&login.data.user?.id===identity);
 if(!login.data.session)throw new Error('No operator session');
 const headers={authorization:'Bearer '+login.data.session.access_token,'content-type':'application/json'};
 const organizations=await fetch(base+'/management/v1/organizations',{headers});const list=await organizations.json();
 check('operator discovers owned organization and role',organizations.ok&&list.data.length===1&&list.data[0].id===recovered.organization&&list.data[0].role==='owner');
 check('unauthenticated organization discovery rejected',(await fetch(base+'/management/v1/organizations')).status===401);
 check('organization creation without a session is refused',(await fetch(base+'/management/v1/organizations',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:'unauthorized-bootstrap'})})).status===401);
 check('the bootstrapped owner is reported as the installation operator',list.operator===true);
 const project=await fetch(base+`/management/v1/organizations/${recovered.organization}/projects`,{method:'POST',headers,body:JSON.stringify({name:'First project'})});
 check('bootstrapped owner creates first project through API',project.status===201);
 const stranger=catalog.createOrganization('unrelated-fixture','Unrelated');
 const filtered=await (await fetch(base+'/management/v1/organizations',{headers})).json();
 check('organization discovery excludes another owner organization',filtered.data.length===1&&filtered.data.every((item:{id:string})=>item.id!==stranger));
 check('operator cannot create project in unrelated organization',(await fetch(base+`/management/v1/organizations/${stranger}/projects`,{method:'POST',headers,body:JSON.stringify({name:'Denied'})})).status===403);
 // Creating a client is an installation decision: the bootstrap owner may, the owner of
 // another client may not.
 const second=await fetch(base+'/management/v1/organizations',{method:'POST',headers,body:JSON.stringify({name:'Second client'})});
 const client=second.status===201?(await second.json()).id:undefined;
 check('installation operator creates a client through API',!!client&&catalog.listOrganizations(recovered.actor).some(item=>item.id===client&&item.role==='owner'));
 check('owner of another client is not an installation operator',!catalog.installationOperator('unrelated-fixture'));
 console.log(`${checks.length} live operator bootstrap checks passed.`);
}finally {
 server?.stop(true);catalog.close();keys.close();
 if(!identity&&journal.read())identity=(await auth.find(journal.read()!.operation))?.id;
 if(identity) {
  const deleted=await fetch(management.auth+'/admin/users/'+identity,{method:'DELETE',headers:{authorization:'Bearer '+internalToken(values.management.jwt,'service_role')}});
  if(!deleted.ok)throw new Error('Test operator cleanup failed');
 }
 rmSync(directory,{recursive:true});
 await Bun.write('.lab/upstream/bootstrap-verification.json',JSON.stringify({scope:'Actual dedicated Auth operator creation, interrupted bootstrap recovery, password verification, private journal, idempotent catalog and authenticated organization discovery; isolated probe catalog, no real operator account retained',checks},null,2));
}
