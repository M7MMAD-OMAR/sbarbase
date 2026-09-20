import {chmodSync,unlinkSync} from 'node:fs';
import {Catalog} from '../src/control/catalog';
import {internalToken} from './upstream-app';
const file='.secrets/upstream/ui-probe.json';
if(!['prepare','viewer','cleanup'].includes(process.argv[2]??''))throw new Error('Use prepare, viewer or cleanup');
const catalog=new Catalog('.lab/upstream/control.sqlite');
const probe=await Bun.file('.lab/upstream/probe.json').json();
const management=await Bun.file('.lab/upstream/management.json').json();
const secrets=await Bun.file('.secrets/upstream/runtime.json').json();
const headers={authorization:'Bearer '+internalToken(secrets.management.jwt,'service_role'),'content-type':'application/json'};
try {
 if(process.argv[2]==='cleanup') {
  const user=await Bun.file(file).json();catalog.setMember('durable-probe-owner',probe.organization,user.id,null);
  const response=await fetch(management.auth+'/admin/users/'+user.id,{method:'DELETE',headers});if(!response.ok)throw new Error('Fixture cleanup failed');
  unlinkSync(file);console.log('UI fixture identity removed.');
 }else if(process.argv[2]==='viewer') {
  const user=await Bun.file(file).json();catalog.setMember('durable-probe-owner',probe.organization,user.id,'viewer');console.log('UI fixture is now a viewer.');
 }else {
  if(await Bun.file(file).exists())throw new Error('A UI fixture already exists');
  const email=`console-${crypto.randomUUID()}@example.com`,password=`Local-${crypto.randomUUID()}`;
  const response=await fetch(management.auth+'/admin/users',{method:'POST',headers,body:JSON.stringify({email,password,email_confirm:true})});
  const user=await response.json();if(!response.ok||!user.id)throw new Error('Fixture creation failed');
  await Bun.write(file,JSON.stringify({id:user.id,email,password}));chmodSync(file,0o600);
  catalog.setMember('durable-probe-owner',probe.organization,user.id,'owner');console.log('Private UI fixture prepared.');
 }
}finally{catalog.close();}
