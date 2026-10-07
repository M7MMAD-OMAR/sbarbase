import {Catalog} from '../src/control/catalog';
import {KeyStore} from '../src/control/keys';
import {Database} from 'bun:sqlite';
import {join} from 'node:path';
import {readFileSync,writeFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';

const [operation,directory]=process.argv.slice(2);
const catalogPath=join(directory,'control.sqlite'),keysPath=join(directory,'managed-keys.sqlite');
if(operation==='create') {
 const c=new Catalog(catalogPath),k=new KeyStore(keysPath);
 const owner=randomUUID(),bootstrap=randomUUID();
 const organization=c.initializeInstallation(bootstrap,owner,'Codec fixture');
 const project=c.createProject(owner,organization,'Full rows');
 const environment=c.createEnvironment(owner,project,'production');
 c.managementSecurity.revoke(owner);
 c.managementSecurity.rate('fixture:rate',3,60000);
 const lease=c.managementSecurity.lease(owner);
 c.close();
 const db=new Database(catalogPath,{strict:true});
 const runtime=(db.query('SELECT runtime FROM provision_jobs WHERE environment=?').get(environment) as any).runtime;
 db.close();
 k.useRuntimeEpoch(()=>3);
 const active=k.issue(runtime),revoked=k.issue(runtime,'secret');
 k.revoke(runtime,revoked.id);
 k.close();
 writeFileSync(join(directory,'private-fixture.json'),JSON.stringify({owner,bootstrap,organization,project,environment,runtime,active,revoked,lease}),{mode:0o600});
 const tc=new Catalog(join(directory,'template-catalog.sqlite'));tc.close();
 const tk=new KeyStore(join(directory,'template-keys.sqlite'));tk.close();
} else if(operation==='verify') {
 const expected=JSON.parse(readFileSync(join(directory,'private-fixture.json'),'utf8'));
 const c=new Catalog(catalogPath),k=new KeyStore(keysPath);
 try {
  const boot=c.installationBootstrap();
  if(boot?.actor!==expected.owner||boot.operation!==expected.bootstrap||boot.organization!==expected.organization)
   throw new Error('Original installation identity did not survive');
  if(c.managementSecurity.epoch(expected.owner)!==1)throw new Error('Revocation epoch lost');
  if(!c.managementSecurity.holds(expected.owner,expected.lease))throw new Error('Lease row lost');
  k.useRuntimeEpoch(()=>3);
  if(k.resolve(expected.runtime,expected.active.token)!=='publishable')throw new Error('Active API key lost');
  if(k.resolve(expected.runtime,expected.revoked.token)!==null)throw new Error('Revoked key resurrected');
  k.useRuntimeEpoch(()=>4);
  if(k.resolve(expected.runtime,expected.active.token)!==null)throw new Error('Runtime epoch binding lost');
 } finally {c.close();k.close();}
} else throw new Error('Unknown original schema fixture command');
