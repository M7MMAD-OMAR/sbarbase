import { createClient } from '@supabase/supabase-js';

const endpoints = await Bun.file('.lab/endpoints.json').json() as Record<string, {auth:string;rest:string}>;
import {createHmac} from 'node:crypto';
import {createGateway, type EnvironmentRoute} from '../src/gateway/handler';
// Lab-only key provisioning. Never expose the JWT signing secrets in output.
const secrets = await Bun.file('.secrets/lab.json').json();
const registry = new Map<string,EnvironmentRoute>();
for (const [env,upstreams] of Object.entries(endpoints)) {
  const encode=(value:unknown)=>Buffer.from(JSON.stringify(value)).toString('base64url');
  const payload=encode({alg:'HS256',typ:'JWT'})+'.'+encode({role:'anon',iss:'sbarbase-lab',exp:Math.floor(Date.now()/1000)+3600});
  const token=payload+'.'+createHmac('sha256',secrets.environments[env].jwt).update(payload).digest('base64url');
  registry.set(env,{...upstreams,enabled:true,keys:['sb_publishable_'+crypto.randomUUID()],anonymousToken:token});
}
const router = Bun.serve({hostname:'127.0.0.1',port:0,fetch:createGateway(registry)});
const results: {check:string;passed:boolean}[] = [];
function check(name:string, ok:boolean) {
  results.push({check:name,passed:ok});
  if (!ok) throw new Error(name);
}
try {
  for (const source of registry.keys()) for (const target of registry.keys()) {
    if(source===target) continue;
    const response=await fetch(`http://127.0.0.1:${router.port}/${target}/rest/v1/lab_items`,{headers:{apikey:registry.get(source)!.keys[0]!}});
    check(`${source} key rejected by ${target} gateway`,response.status===401);
  }
  const suffix = crypto.randomUUID();
  for (const env of Object.keys(endpoints)) {
    const client = createClient(`http://127.0.0.1:${router.port}/${env}`, registry.get(env)!.keys[0]!, {
      auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false}
    });
    const {data:signup,error:signupError} = await client.auth.signUp({email:`sdk-${suffix}@example.com`,password:`Local-${suffix}`});
    check(`${env} SDK signup`,!signupError && !!signup.session);
    const {data:refreshed,error:refreshError} = await client.auth.refreshSession();
    check(`${env} SDK refresh`,!refreshError && !!refreshed.session);
    const {data:insert,error:insertError} = await client.from('lab_items').insert({owner_id:signup.user!.id,value:suffix}).select().single();
    check(`${env} SDK insert/select`,!insertError && insert?.value===suffix);
    const {error:updateError} = await client.from('lab_items').update({value:suffix+'-updated'}).eq('id',insert.id);
    check(`${env} SDK update`,!updateError);
    const {data:items,error:readError} = await client.from('lab_items').select().eq('id',insert.id);
    check(`${env} SDK read`,!readError && items?.length===1 && items[0].value===suffix+'-updated');
    const {error:deleteError} = await client.from('lab_items').delete().eq('id',insert.id);
    const {data:after} = await client.from('lab_items').select().eq('id',insert.id);
    check(`${env} SDK delete`,!deleteError && after?.length===0);
    const {error:logoutError}=await client.auth.signOut();
    check(`${env} SDK logout`,!logoutError);
  }
  console.log(`${results.length} Supabase SDK checks passed through environment-key gateway.`);
} finally {
  await Bun.write('.lab/sdk-verification.json',JSON.stringify({sdk:'2.116.0',scope:'environment key gateway with ephemeral lab publishable keys, Auth and REST only',checks:results},null,2));
  router.stop(true);
}
