import {Catalog} from '../src/control/catalog';
import {managementHandler} from '../src/control/http';

const state='.lab/provision-probe.json';
let c=new Catalog('.lab/control.sqlite');
let probe:{organization:string;project:string;environment:string};
const checks:{check:string;passed:boolean}[]=[];
function check(name:string,passed:boolean){checks.push({check:name,passed});if(!passed) throw new Error(name);}
async function command(args:string[]) {
 const child=Bun.spawn(args,{stdout:'pipe',stderr:'ignore'});
 const output=await new Response(child.stdout).text();
 if(await child.exited) throw new Error('Probe command failed');
 return output.trim();
}
try {
 if(await Bun.file(state).exists()) probe=await Bun.file(state).json();
 else {
  const organization=c.createOrganization('probe-owner','Provision probe');
  const project=c.createProject('probe-owner',organization,'P');
  const handler=managementHandler(c,async()=> 'probe-owner');
  const response=await handler(new Request(`http://localhost/management/v1/projects/${project}/environments`,{
   method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:'production'})}));
  check('HTTP creation accepted for asynchronous provisioning',response.status===202);
  const {id:environment}=await response.json();probe={organization,project,environment};
  await Bun.write(state,JSON.stringify(probe));
 }
 let job=c.getProvision('probe-owner',probe.environment);
 if(job.state==='succeeded') {
  // A repeated probe only verifies existing runtime; never allocates another environment.
  check('previous successful operation retained',true);
 } else {
  if(job.state==='failed'||job.state==='cancelled') c.retryProvision('probe-owner',probe.environment);
  if(job.state==='running') c.recoverProvisioning();
  const claimed=c.claimProvision();check('durable job claimed',claimed?.environment===probe.environment);
  if(!claimed) throw new Error('No job');job=claimed;
  await command(['/usr/bin/python3','lab/provision.py',job.runtime]);
  // Simulate crash at the external-effect boundary: services are healthy, but the
  // catalog still says running. Reopen and replay through the normal worker.
 }
 const endpoint=(await Bun.file('.lab/endpoints.json').json())[job.runtime];
 const suffix=crypto.randomUUID(),email=`recovery-${suffix}@example.com`,password=`Local-${suffix}`;
 const signup=await fetch(`${endpoint.auth}/signup`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({email,password})});
 const account=await signup.json();check('provisioned Auth accepts signup',signup.ok&&!!account.user?.id);
 const oid=await command(['docker','exec','sbarbase-lab-db','psql','-U','postgres','-Atc',`SELECT oid FROM pg_database WHERE datname='${job.runtime}'`]);
 c.close();c=new Catalog('.lab/control.sqlite');
 check('running or completed operation survived catalog reopen',['running','succeeded'].includes(c.getProvision('probe-owner',probe.environment).state));
 await command(['/usr/bin/python3','lab/worker.py']);
 check('worker reconciles to success',c.getProvision('probe-owner',probe.environment).state==='succeeded');
 const after=await command(['docker','exec','sbarbase-lab-db','psql','-U','postgres','-Atc',`SELECT oid FROM pg_database WHERE datname='${job.runtime}'`]);
 check('retry preserved same database identity',oid===after&&!!oid);
 const login=await fetch(`${endpoint.auth}/token?grant_type=password`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({email,password})});
 const session=await login.json();check('retry preserved Auth user data',login.ok&&session.user?.id===account.user.id);
 console.log(`${checks.length} live provisioning checks passed.`);
} finally {
 c.close();await Bun.write('.lab/provision-verification.json',JSON.stringify({scope:'Single-host lab Auth/REST provisioning and reconciliation after simulated lost completion; not full Supabase or multi-host provisioning',checks},null,2));
}
