import {createClient} from '@supabase/supabase-js';
import {createHmac} from 'node:crypto';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {controlHandler} from '../src/control/handler';
import {managementIdentity} from '../src/control/auth';
import {managedGateway} from '../src/gateway/managed';

const probe=await Bun.file('.lab/provision-probe.json').json();
const endpoints=await Bun.file('.lab/endpoints.json').json();
// Programmatic lab signing-key access, never log raw configuration.
const secrets=await Bun.file('.secrets/lab.json').json();
const catalog=new Catalog('.lab/control.sqlite'),keys=new KeyStore('.secrets/managed-keys.sqlite');
const job=catalog.getProvision('probe-owner',probe.environment);
if(job.state!=='succeeded') throw new Error('Run provisioning probe first');
const runtime=job.runtime;
const suffix=crypto.randomUUID(),password=`Local-${suffix}`;
async function signup(auth:string,email:string) {
 const response=await fetch(`${auth}/signup`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({email,password})});
 const data=await response.json();if(!response.ok||!data.access_token) throw new Error('Signup failed');return data;
}
const managementUser=await signup(endpoints.a_stage.auth,`control-${suffix}@example.com`);
catalog.setMember('probe-owner',probe.organization,managementUser.user.id,'owner');
const transport=(async(input,init)=>{
 if(String(input)!=='http://management.invalid/auth/v1/user') throw new Error('Unexpected management endpoint');
 return fetch(`${endpoints.a_stage.auth}/user`,init);
}) as typeof fetch;
const control=controlHandler(catalog,keys,managementIdentity('http://management.invalid','lab-key',transport));
const encode=(value:unknown)=>Buffer.from(JSON.stringify(value)).toString('base64url');
const message=encode({alg:'HS256',typ:'JWT'})+'.'+encode({role:'anon',exp:Math.floor(Date.now()/1000)+3600});
const anonymousToken=message+'.'+createHmac('sha256',secrets.environments[runtime].jwt).update(message).digest('base64url');
const gateway=managedGateway(catalog,keys,requested=>requested===runtime?{...endpoints[runtime],keys:[],anonymousToken,enabled:true}:undefined);
const server=Bun.serve({hostname:'127.0.0.1',port:0,fetch:request=>new URL(request.url).pathname.startsWith('/management/')?control(request):gateway(request)});
const base=`http://127.0.0.1:${server.port}`,path=`/management/v1/environments/${probe.environment}`;
const checks:{check:string;passed:boolean}[]=[];let issuedId:string|undefined;
function check(name:string,passed:boolean){checks.push({check:name,passed});if(!passed)throw new Error(name);}
async function admin(tail:string,method='GET',token=managementUser.access_token) {
 return fetch(base+path+tail,{method,headers:{authorization:`Bearer ${token}`}});
}
try {
 const connection=await admin('/connection');check('authorized connection discovery',connection.status===200);
 const info=await connection.json();check('stable runtime route matches provisioned environment',info.apiPath===`/${runtime}`);
 const issued=await admin('/keys','POST');check('management Auth permits key issuance',issued.status===201);
 const credential=await issued.json();issuedId=credential.id;
 const list=await admin('/keys');check('key list never discloses raw token',list.status===200&&!(await list.text()).includes(credential.token));
 const client=createClient(base+info.apiPath,credential.token,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false}});
 const {data,error}=await client.auth.signUp({email:`application-${suffix}@example.com`,password});
 check('issued key connects SDK to provisioned Auth',!error&&!!data.session);
 check('application identity cannot issue management keys',(await admin('/keys','POST',data.session!.access_token)).status===401);
 const read=await client.from('lab_items').select();check('issued key connects SDK to provisioned REST',!read.error);
 const revoked=await admin(`/keys/${issuedId}`,'DELETE');check('authorized key revocation',revoked.status===200);
 const denied=await fetch(base+info.apiPath+'/rest/v1/lab_items',{headers:{apikey:credential.token}});
 check('revocation immediately removes gateway access',denied.status===401);
 console.log(`${checks.length} live connection/key checks passed.`);
} finally {
 if(issuedId)keys.revoke(runtime,issuedId);
 catalog.setMember('probe-owner',probe.organization,managementUser.user.id,null);
 server.stop(true);catalog.close();keys.close();
 await Bun.write('.lab/connection-verification.json',JSON.stringify({scope:'Provisioned Auth/REST via management-issued publishable key, live separate Auth realms and loopback gateway',checks},null,2));
}
