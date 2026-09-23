import {readFileSync} from 'node:fs';
import {createHmac} from 'node:crypto';
import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {application} from '../src/control/application';
import {readJsonCached} from '../src/http/cached-json';

export const managementPublishableKey='sb_publishable_sbarbase_local_management';
export function internalToken(secret:string,role:string) {
 const encode=(value:unknown)=>Buffer.from(JSON.stringify(value)).toString('base64url');
 const message=encode({alg:'HS256',typ:'JWT'})+'.'+encode({role,iss:'sbarbase-internal'});
 return message+'.'+createHmac('sha256',secret).update(message).digest('base64url');
}
export function openUpstreamApplication() {
 const load=(path:string)=>JSON.parse(readFileSync(path,'utf8'));
 const secrets=load('.secrets/upstream/runtime.json');
 const management=load('.lab/upstream/management.json');
 const catalog=new Catalog('.lab/upstream/control.sqlite');
 const keys=new KeyStore('.secrets/upstream/managed-keys.sqlite');
 const handler=application(catalog,keys,{auth:management.auth,publishableKey:managementPublishableKey,
  anonymousToken:internalToken(secrets.management.jwt,'anon')},runtime=>{
  // Refresh private configuration and endpoints after provisioning or restart: the cache
  // rereads either file as soon as it changes.
  const endpoints=readJsonCached('.lab/upstream/endpoints.json') as Record<string,any>;
  const current=readJsonCached('.secrets/upstream/runtime.json') as {environments:Record<string,any>};
  if(!endpoints[runtime]||!current.environments[runtime])return undefined;
  return {...endpoints[runtime],keys:[],anonymousToken:internalToken(current.environments[runtime].jwt,'anon'),enabled:true};
 });
 return {handler,catalog,keys,close(){catalog.close();keys.close();}};
}
