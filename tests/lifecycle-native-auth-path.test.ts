import {test,expect} from 'bun:test';
import {mkdirSync,mkdtempSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Catalog} from '../src/control/catalog';
import {LifecycleAuthorizationStore,NativeLifecycleAuthority} from '../src/control/lifecycle-authority';
import {managementToken,factorId,otherFactorId,sessionId,verifiedFactor} from './management-fixture';

/** Real loopback HTTP with a stubbed retained-realm proof, never installation credentials. */
function fixture(){
 const root=mkdtempSync(join(tmpdir(),'lifecycle-native-path-')),calls:string[]=[];
 let factors=[verifiedFactor],redirect=false;
 const now=Math.floor(Date.now()/1000),token=managementToken('owner','aal2',sessionId,now);
 const server=Bun.serve({hostname:'127.0.0.1',port:0,fetch(request){
  const path=new URL(request.url).pathname;calls.push(path);
  if(path!=='/user')return Response.json({message:'Native GoTrue has no SDK prefix'},{status:404});
  if(request.headers.get('authorization')!=='Bearer '+token||request.headers.get('apikey')!=='native-anon')return new Response(null,{status:401});
  if(redirect)return Response.redirect(new URL('/redirect-target',request.url).href,302);
  return Response.json({id:'owner',is_anonymous:false,factors});
 }});
 mkdirSync(join(root,'lab'));mkdirSync(join(root,'.lab/upstream'),{recursive:true});
 let endpoint=server.url.origin;
 function realm(){writeFileSync(join(root,'lab/lifecycle_auth_realm.py'),'import json\nprint(json.dumps('+JSON.stringify({auth:endpoint,key:'native-anon',process:'a'.repeat(64)+':'+ 'b'.repeat(64)})+'))\n');}
 realm();const catalog=new Catalog(join(root,'.lab/upstream/control.sqlite'));catalog.initializeInstallation('bootstrap','owner','Installation');
 const store=LifecycleAuthorizationStore.installed(root);catalog.managementSecurity.grant('owner',sessionId,factorId,now,now+3600,0);
 const request=new Request('http://local',{headers:{authorization:'Bearer '+token}});
 return {catalog,store,request,calls,server,
  set factors(value:typeof factors){factors=value;},set redirect(value:boolean){redirect=value;},
  set endpoint(value:string){endpoint=value;realm();},
  close(){server.stop(true);catalog.close();rmSync(root,{recursive:true,force:true});}};
}

test('installed request and refresh use real native /user and retain the exact original grant',async()=>{
 const f=fixture();try{
  expect((await fetch(new URL('/auth/v1/user',f.server.url))).status).toBe(404);f.calls.length=0;
  const authority=await NativeLifecycleAuthority.request(f.catalog,f.store,f.request);
  expect(authority.actor()).toBe('owner');expect(f.calls).toEqual(['/user','/user']);
  await authority.refresh();expect(f.calls).toEqual(['/user','/user','/user']);
  f.factors=[{...verifiedFactor,id:otherFactorId}];
  f.catalog.managementSecurity.grant('owner',sessionId,otherFactorId,Math.floor(Date.now()/1000),Math.floor(Date.now()/1000)+3600,0);
  await expect(authority.refresh()).rejects.toThrow('revoked');
 }finally{f.close();}
});
test('installed native path mapping refuses redirects and a non-root realm path',async()=>{
 const f=fixture();try{
  f.redirect=true;
  await expect(NativeLifecycleAuthority.request(f.catalog,f.store,f.request)).rejects.toThrow();
  expect(f.calls).toContain('/user');expect(f.calls).not.toContain('/redirect-target');
  f.redirect=false;f.calls.length=0;f.endpoint=f.server.url.origin+'/foreign-prefix';
  await expect(NativeLifecycleAuthority.request(f.catalog,f.store,f.request)).rejects.toThrow('revoked');expect(f.calls).toEqual([]);
 }finally{f.close();}
});
test('installed lifecycle store still refuses caller-supplied identity transports',async()=>{
 const f=fixture();try{
  const transport=Object.assign(async()=>Response.json({id:'owner',factors:[verifiedFactor]}),{preconnect(){}}) as typeof fetch;
  await expect(NativeLifecycleAuthority.request(f.catalog,f.store,f.request,transport)).rejects.toThrow('revoked');expect(f.calls).toEqual([]);
 }finally{f.close();}
});
