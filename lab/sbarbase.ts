/** `sbarbase`: one operator command for the common tasks. It adds no capability of its own.
 *
 * Every subcommand is one of three things:
 * - a delegation to an existing tool, run as a child with the same arguments an operator
 *   would type (lab/backup.py, lab/upgrade.py, journalctl, docker logs), whose exit code is
 *   passed through unchanged;
 * - a call to the management API the console uses (add-environment, rotate-key, studio),
 *   after signing in to the management Auth realm with the operator's own account, so the
 *   same membership checks, capacity refusal and audit apply;
 * - a read-only look at the local state files the runtime already writes (status,
 *   environments), so it still answers when the console or management Auth is down.
 *
 * Written in Bun TypeScript because the write paths are HTTP calls to the management API,
 * the Python tools are only ever run as child processes, and Bun is already on every host.
 * Passwords are never read from arguments; the access token is never stored or printed.
 */
import {Database} from 'bun:sqlite';
import {existsSync,lstatSync,readdirSync,readFileSync} from 'node:fs';
import {join,resolve} from 'node:path';
import {managementPublishableKey} from './upstream-app';

export const EXIT={ok:0,failed:1,usage:2,refused:3} as const;
const PYTHON='/usr/bin/python3';
const UNIT='sbarbase.service';
const PREFIX='sbarbase-durable';
const RUNTIME=/^e_[a-f0-9]{24}$/;
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const STAMP=/^\d{8}T\d{6}Z$/;

export type Deps={
 root:string;
 env:Record<string,string|undefined>;
 /** A child with inherited stdio, in the installation root; resolves to its exit code. */
 run:(argv:string[])=>Promise<number>;
 /** A short child whose standard output is read, for `systemctl is-active`. */
 capture:(argv:string[])=>Promise<{code:number;stdout:string}>;
 fetch:typeof fetch;
 /** One line typed at the terminal; `secret` turns echo off. */
 prompt:(question:string,secret?:boolean)=>Promise<string>;
 stdin:()=>Promise<string>;
 isTTY:boolean;
 out:(text:string)=>void;
 err:(text:string)=>void;
 pidAlive:(pid:number)=>boolean;
 /** Inside the compose control-plane container. */
 inContainer:boolean;
 /** The installed systemd unit, when there is one. */
 unitPath:string;
};

class Refusal extends Error {constructor(message:string,readonly code:number=EXIT.refused){super(message);}}
class Usage extends Refusal {constructor(message:string){super(message,EXIT.usage);}}

const HELP=`sbarbase: the operator command for one Sbarbase installation

Usage: sbarbase <command> [arguments] [options]

Commands
  status [--json]                     service, runtime, environments, routing, last backup, notifications
  environments [--json]               every environment with its id, runtime id and state
  backup now [<environment>] [--keep N]
                                      back up one environment, or every one (lab/backup.py create)
  backups list [<environment>]        complete backups (lab/backup.py list)
  restore <environment> <backup> [--yes]
                                      replace one environment with a backup of itself (lab/backup.py restore)
  add-environment <project> <name>    create an environment through the management API, as the console does
  rotate-key <environment> [--revoke <key id>]
                                      issue a new publishable key, show it once, then revoke the old one
  upgrade [check|start|status|rollback] [--to REF]
                                      lab/upgrade.py; without a subcommand it runs check
  studio start|stop <environment>     ask for Studio through the management API; the supervisor runs it
  share <environment> [<n>]           show an environment's gateway share, or set it (operators only)
  logs [supervisor|auth|rest|storage|database] [<environment>] [--lines N] [--follow]
                                      journalctl for the supervisor, docker logs for a service
  help [<command>]                    this text

<environment> is the environment id from the console, its runtime id (e_ and 24 hex), or
<project>/<name>. <project> is a project id or name, or <client>/<project> when names repeat.

Sign-in, for add-environment, rotate-key, studio and share: your own management account.
  --email ADDRESS        or SBARBASE_EMAIL; asked at the terminal otherwise
  --password-stdin       read the password from standard input; asked without echo otherwise
  --operator-file PATH   a private 0600 JSON file with email and password (lab/operator_file.py)
A password is never accepted as an argument. The session token is never stored or printed.

Exit codes
  0  done, or everything checked is healthy
  1  the operation or a delegated tool failed; a delegated tool's own code is passed through
  2  usage error
  3  refused or not confirmed: nothing was changed

Run it from the installation directory as the service account, for example:
  sudo -u sbarbase /opt/sbarbase/deploy/sbarbase status
With Docker: docker compose exec sbarbase deploy/sbarbase status`;

const COMMAND_HELP:Record<string,string>={
 status:`sbarbase status [--json]

Reads the state the runtime already writes, without signing in: the supervisor service, the
console, each environment with its routing, health and last backup, and notifications not yet
delivered. Exits 0 when the console runs and every published environment answers, 1 otherwise.
--json prints the same facts as one JSON object.`,
 environments:`sbarbase environments [--json]

Every environment in the control catalog: client, project, name, environment id, runtime id and
provisioning state. Read only, without signing in.`,
 backup:`sbarbase backup now [<environment>] [--keep N]

Runs lab/backup.py create for one environment, or for every environment when none is named.
--keep defaults to SBARBASE_BACKUP_KEEP, read from this shell or from the installed unit, so a
manual backup prunes like the daily one. When neither sets it, lab/backup.py keeps 7.`,
 backups:`sbarbase backups list [<environment>]

Runs lab/backup.py list.`,
 restore:`sbarbase restore <environment> <backup> [--yes]

Runs lab/backup.py restore after you confirm. Rows, users and files written after the backup
are gone from that environment. The replaced state is kept aside until
lab/backup.py discard-previous. Without a terminal, --yes is required.`,
 'add-environment':`sbarbase add-environment <project> <name>

Signs in and asks the management API for a new environment, exactly as the console does. The
worker provisions it; follow it with sbarbase environments.`,
 'rotate-key':`sbarbase rotate-key <environment> [--revoke <key id>]

Signs in, issues a new publishable key through the key API, prints it once on standard output,
then revokes the old key. With more than one active key, name the one to revoke with --revoke.`,
 upgrade:`sbarbase upgrade [check|start|status|rollback] [--to REF]

Runs lab/upgrade.py. Without a subcommand it runs check, which changes nothing.`,
 studio:`sbarbase studio start|stop <environment>

Signs in and asks for Studio through the management API, as the console's Studio button does.
The supervisor starts or stops it (lab/studio.py). Open it from the console.`,
 share:`sbarbase share <environment> [<n>]

Signs in and reads the environment's guaranteed share of the application gateway through the
management API, with the allocation across the installation when your account is an
installation operator. With <n>, sets the share through the same route the console uses; only
installation operators may, and the API refuses a share that would exceed the gateway.`,
 logs:`sbarbase logs [supervisor|auth|rest|storage|database] [<environment>] [--lines N] [--follow]

supervisor (the default) reads journalctl -u sbarbase.service; with Docker, run
docker compose logs sbarbase on the host instead. auth and rest need an environment; storage
and database are shared by every environment. --lines defaults to 100.`,
};

type Parsed={command:string;args:string[];flags:Record<string,string|boolean>};
const VALUE_FLAGS=new Set(['--email','--operator-file','--keep','--to','--revoke','--lines']);
const BOOLEAN_FLAGS=new Set(['--json','--yes','--password-stdin','--follow','--help']);
const ALIASES:Record<string,string>={'-h':'--help','-y':'--yes','-f':'--follow','-n':'--lines'};
const ALLOWED:Record<string,string[]>={
 status:['--json'],environments:['--json'],backup:['--keep'],backups:[],restore:['--yes'],
 'add-environment':['--email','--operator-file','--password-stdin'],
 'rotate-key':['--email','--operator-file','--password-stdin','--revoke'],
 upgrade:['--to'],studio:['--email','--operator-file','--password-stdin'],share:['--email','--operator-file','--password-stdin'],logs:['--lines','--follow'],help:[],
};

export function parse(argv:string[]):Parsed {
 const args:string[]=[],flags:Record<string,string|boolean>={};
 for(let index=0;index<argv.length;index++) {
  const raw=argv[index]!;
  if(!raw.startsWith('-')||raw==='-'){args.push(raw);continue;}
  const [given,inline]=raw.includes('=')?[raw.slice(0,raw.indexOf('=')),raw.slice(raw.indexOf('=')+1)]:[raw,undefined];
  const name=ALIASES[given]??given;
  if(name==='--password')throw new Usage('A password is never accepted as an argument; use --password-stdin or the prompt');
  if(BOOLEAN_FLAGS.has(name)){if(inline!==undefined)throw new Usage(`${name} takes no value`);flags[name]=true;continue;}
  if(!VALUE_FLAGS.has(name))throw new Usage(`Unknown option ${given}`);
  const value=inline??argv[++index];
  if(value===undefined||value==='')throw new Usage(`${name} needs a value`);
  flags[name]=value;
 }
 const command=args.shift()??'help';
 if(!(command in ALLOWED))throw new Usage(`Unknown command ${command}`);
 for(const flag of Object.keys(flags))
  if(flag!=='--help'&&!ALLOWED[command]!.includes(flag))throw new Usage(`${flag} does not apply to ${command}`);
 return {command,args,flags};
}

function arity(parsed:Parsed,min:number,max:number) {
 if(parsed.args.length<min||parsed.args.length>max)throw new Usage(`Wrong number of arguments for ${parsed.command}`);
}

function positiveInteger(value:string|boolean|undefined,name:string):string|undefined {
 if(value===undefined)return undefined;
 if(typeof value!=='string'||!/^[1-9]\d{0,5}$/.test(value))throw new Usage(`${name} must be a positive whole number`);
 return value;
}

// ---- Local, read-only state ----------------------------------------------------------

type Row={id:string|null;client:string|null;project:string|null;name:string|null;runtime:string|null;state:string|null};
type Routing={revision:number;maintenance:boolean;placement:{auth?:string;rest?:string}|null};
type Local={rows:Row[];routing:Map<string,Routing>;catalog:'ok'|'missing'|'unreadable';
 notifications:{undelivered:number;recent:{kind:string;severity:string;reason:string;at:string;channels:Record<string,string>}[]}|null};

const state=(deps:Deps,...parts:string[])=>join(deps.root,'.lab','upstream',...parts);

function readJson(path:string):any {
 try {return JSON.parse(readFileSync(path,'utf8'));} catch {return undefined;}
}

/** The control catalog opened read only, never through Catalog, whose constructor writes. */
function readCatalog(deps:Deps):Local {
 const path=state(deps,'control.sqlite');
 const local:Local={rows:[],routing:new Map(),catalog:'missing',notifications:null};
 if(!existsSync(path))return local;
 let database:Database|undefined;
 try {
  database=new Database(path,{readonly:true});
  local.rows=database.query<Row,[]>(`SELECT e.id id,o.name client,p.name project,e.name name,j.runtime runtime,j.state state
   FROM environments e JOIN projects p ON p.id=e.project JOIN organizations o ON o.id=p.organization
   LEFT JOIN provision_jobs j ON j.environment=e.id ORDER BY o.name,p.name,e.name`).all();
  for(const row of database.query<{runtime:string;revision:number;maintenance:number;placement:string|null},[]>(
   'SELECT runtime,revision,maintenance,placement FROM runtime_routing').all())
   local.routing.set(row.runtime,{revision:row.revision,maintenance:row.maintenance===1,
    placement:row.placement===null?null:JSON.parse(row.placement)});
  // Kind, severity, reason code and delivery state only: never the detail, a recipient or a delivery error.
  const deliveries=database.query<{id:string;kind:string;severity:string;reason:string;at:number;channel:string;state:string},[]>(
   `SELECT o.id id,o.kind kind,o.severity severity,o.reason reason,o.at at,d.channel channel,d.state state
    FROM notification_outbox o JOIN notification_delivery d ON d.event=o.id ORDER BY o.at DESC,d.channel`).all();
  const events=new Map<string,{kind:string;severity:string;reason:string;at:string;channels:Record<string,string>}>();
  for(const row of deliveries) {
   const event=events.get(row.id)??{kind:row.kind,severity:row.severity,reason:row.reason,at:new Date(row.at).toISOString(),channels:{}};
   event.channels[row.channel]=row.state;events.set(row.id,event);
  }
  const pending=[...events.values()].filter(event=>Object.values(event.channels).some(value=>value!=='delivered'));
  local.notifications={undelivered:pending.length,recent:pending.slice(0,5)};
  local.catalog='ok';
 } catch {
  local.catalog='unreadable';
 } finally {database?.close();}
 return local;
}

/** Complete backups of one runtime, newest last: a stamped directory with its manifest. */
export function backupsOf(root:string,runtime:string):string[] {
 const folder=join(root,'.lab','backups',runtime);
 try {
  return readdirSync(folder).filter(name=>STAMP.test(name)&&existsSync(join(folder,name,'manifest.json'))).sort();
 } catch {return [];}
}

async function health(deps:Deps,url:string|undefined,suffix:string):Promise<number|'unreachable'|'unpublished'> {
 if(!url)return 'unpublished';
 try {
  const response=await deps.fetch(url.replace(/\/$/,'')+suffix,{signal:AbortSignal.timeout(3000),redirect:'manual'});
  await response.body?.cancel();
  return response.status;
 } catch {return 'unreachable';}
}

async function service(deps:Deps) {
 if(deps.inContainer)return {manager:'container',state:'running'};
 if(!existsSync(deps.unitPath))return {manager:'none',state:'not installed'};
 try {
  const result=await deps.capture(['systemctl','is-active',UNIT]);
  return {manager:'systemd',state:result.stdout.trim()||'unknown'};
 } catch {return {manager:'systemd',state:'unknown'};}
}

function consoleState(deps:Deps) {
 const server=readJson(state(deps,'server.json'));
 const pid=server&&Number.isSafeInteger(server.pid)&&server.pid>0?server.pid as number:null;
 const running=pid!==null&&deps.pidAlive(pid);
 return {running,url:running&&typeof server.url==='string'?server.url as string:null};
}

export async function collectStatus(deps:Deps) {
 const local=readCatalog(deps);
 const published:Record<string,{auth?:string;rest?:string}>=readJson(state(deps,'endpoints.json'))??{};
 const known=new Set(local.rows.map(row=>row.runtime).filter(Boolean));
 // A published runtime the catalog does not list (a lab fixture) is still shown.
 const rows=[...local.rows,...Object.keys(published).filter(runtime=>RUNTIME.test(runtime)&&!known.has(runtime))
  .map(runtime=>({id:null,client:null,project:null,name:null,runtime,state:null}))];
 const environments=await Promise.all(rows.map(async row=>{
  const runtime=row.runtime&&RUNTIME.test(row.runtime)?row.runtime:null;
  const routing=runtime?local.routing.get(runtime)??{revision:0,maintenance:false,placement:null}:null;
  // A moved environment is served from its routing record, every other one from what the runtime published.
  const target=routing?.placement??(runtime?published[runtime]:undefined);
  const isPublished=!!(runtime&&published[runtime]);
  const backups=runtime?backupsOf(deps.root,runtime):[];
  return {id:row.id,client:row.client,project:row.project,name:row.name,runtime,state:row.state,published:isPublished,
   routing:routing&&{mode:routing.maintenance?'maintenance':'serving',placement:routing.placement?'moved':'source',revision:routing.revision},
   health:isPublished?{auth:await health(deps,target?.auth,'/health'),rest:await health(deps,target?.rest,'/')}:null,
   lastBackup:backups.at(-1)??null,backups:backups.length};
 }));
 const status={service:await service(deps),console:consoleState(deps),catalog:local.catalog,environments,
  notifications:local.notifications};
 const healthy=status.console.running&&environments.every(environment=>!environment.health||
  environment.routing?.mode==='maintenance'||(environment.health.auth===200&&environment.health.rest===200));
 return {healthy,status};
}

const label=(row:{client:string|null;project:string|null;name:string|null;runtime:string|null})=>
 row.name?`${row.client}/${row.project}/${row.name}`:`(not in catalog) ${row.runtime}`;

async function statusCommand(deps:Deps,json:boolean) {
 const {healthy,status}=await collectStatus(deps);
 if(json){deps.out(JSON.stringify({healthy,...status},null,1));return healthy?EXIT.ok:EXIT.failed;}
 deps.out(`Service        ${status.service.manager==='systemd'?`${UNIT}: ${status.service.state}`:status.service.manager==='container'
  ?'Docker control-plane container':'no systemd unit installed (a hand-started lab/dev.py?)'}`);
 deps.out(`Console        ${status.console.running?`running at ${status.console.url}`:'not running'}`);
 if(status.catalog==='unreadable')
  deps.out('Catalog        not readable by this user; run as the service account (sudo -u sbarbase ...)');
 deps.out(`Environments   ${status.environments.length}`);
 for(const environment of status.environments) {
  const parts=[label(environment),environment.runtime??'no runtime',environment.state??'unknown'];
  if(environment.routing)parts.push(environment.routing.mode+(environment.routing.placement==='moved'?' (moved)':''));
  if(environment.health)parts.push(`auth ${environment.health.auth}, rest ${environment.health.rest}`);
  else if(environment.runtime)parts.push('not published');
  parts.push(environment.lastBackup?`last backup ${environment.lastBackup}`:'no backup');
  deps.out('  '+parts.join('  '));
 }
 if(status.notifications) {
  deps.out(`Notifications  ${status.notifications.undelivered} not delivered`);
  for(const event of status.notifications.recent)
   deps.out(`  ${event.at}  ${event.severity}  ${event.kind}  ${Object.entries(event.channels).map(([channel,value])=>channel+' '+value).join(', ')}`);
 }
 return healthy?EXIT.ok:EXIT.failed;
}

async function environmentsCommand(deps:Deps,json:boolean) {
 const local=readCatalog(deps);
 if(local.catalog!=='ok')throw new Refusal(local.catalog==='missing'?'No control catalog yet: the installation has not started'
  :'The control catalog is not readable by this user; run as the service account (sudo -u sbarbase ...)');
 if(json){deps.out(JSON.stringify(local.rows,null,1));return EXIT.ok;}
 if(!local.rows.length){deps.out('No environments yet.');return EXIT.ok;}
 for(const row of local.rows)deps.out(`${label(row)}  ${row.id}  ${row.runtime??'-'}  ${row.state??'-'}`);
 return EXIT.ok;
}

/** An environment id for the management API, from an id, a runtime id or project/name. */
export function environmentId(deps:Deps,reference:string):string {
 if(UUID.test(reference))return reference;
 const local=readCatalog(deps);
 if(local.catalog!=='ok')throw new Refusal('Name the environment by its id: the control catalog is not readable here');
 const matches=RUNTIME.test(reference)?local.rows.filter(row=>row.runtime===reference):local.rows.filter(row=>{
  const parts=reference.split('/');
  if(parts.length===2)return row.project===parts[0]&&row.name===parts[1];
  if(parts.length===3)return row.client===parts[0]&&row.project===parts[1]&&row.name===parts[2];
  return false;
 });
 if(matches.length===1&&matches[0]!.id)return matches[0]!.id;
 if(matches.length>1)throw new Refusal(`${reference} names more than one environment; use its id: ${matches.map(row=>row.id).join(', ')}`);
 throw new Refusal(`No environment ${reference}`);
}

/** What lab/backup.py accepts: a runtime id or an environment id, resolved from project/name here. */
function backupTarget(deps:Deps,reference:string):string {
 return RUNTIME.test(reference)||UUID.test(reference)?reference:environmentId(deps,reference);
}

function runtimeOf(deps:Deps,reference:string):string {
 if(RUNTIME.test(reference))return reference;
 const id=environmentId(deps,reference);
 const row=readCatalog(deps).rows.find(candidate=>candidate.id===id);
 if(!row?.runtime)throw new Refusal(`${reference} has no runtime yet`);
 return row.runtime;
}

// ---- Management API, as the console calls it -----------------------------------------

type Credentials={email:string;password:string};

async function credentials(deps:Deps,flags:Parsed['flags']):Promise<Credentials> {
 if(typeof flags['--operator-file']==='string') {
  const path=flags['--operator-file'];
  let metadata;
  try {metadata=lstatSync(path);} catch {throw new Refusal('The operator file does not exist');}
  if(!metadata.isFile())throw new Refusal('The operator file must be a regular file');
  if(typeof process.getuid==='function'&&metadata.uid!==process.getuid())throw new Refusal('The operator file must be owned by the running user');
  if((metadata.mode&0o777)!==0o600)throw new Refusal('The operator file must be mode 0600');
  const payload=readJson(path);
  if(!payload||typeof payload.email!=='string'||typeof payload.password!=='string')
   throw new Refusal('The operator file must hold email and password');
  return {email:payload.email,password:payload.password};
 }
 let email=typeof flags['--email']==='string'?flags['--email']:deps.env.SBARBASE_EMAIL??'';
 if(!email) {
  if(!deps.isTTY)throw new Refusal('No terminal to ask for the email: pass --email or set SBARBASE_EMAIL',EXIT.usage);
  email=(await deps.prompt('Operator email: ')).trim();
 }
 let password:string;
 if(flags['--password-stdin'])password=(await deps.stdin()).split('\n')[0]??'';
 else {
  if(!deps.isTTY)throw new Refusal('No terminal to ask for the password: use --password-stdin or --operator-file',EXIT.usage);
  password=await deps.prompt('Password: ',true);
 }
 if(!email||!password)throw new Refusal('An email and a password are required',EXIT.usage);
 return {email,password};
}

type Api=(path:string,method?:string,body?:unknown)=>Promise<any>;

/** The words the console shows for the fixed messages the management API answers with. */
const CONFLICTS:Record<string,string>={
 'Name already used':'That name is already used in this project.',
 'Environment capacity reached':'This installation has reached its environment limit.',
 'Environment is not ready':'This environment is not provisioned yet.',
 'Studio is not running':'Studio is not running yet.',
 'Shares exceed gateway capacity':'That share would take the shares of all environments past the gateway capacity.',
};

async function signedIn<T>(deps:Deps,flags:Parsed['flags'],work:(api:Api)=>Promise<T>):Promise<T> {
 const base=consoleState(deps).url;
 if(!base)throw new Refusal('The console is not running, so the management API cannot be reached',EXIT.failed);
 const given=await credentials(deps,flags);
 const headers={apikey:managementPublishableKey,'content-type':'application/json'};
 let response:Response;
 try {
  response=await deps.fetch(base+'/management/auth/v1/token?grant_type=password',
   {method:'POST',headers,body:JSON.stringify({email:given.email,password:given.password}),redirect:'error'});
 } catch {throw new Refusal('Management sign-in is unreachable',EXIT.failed);}
 const session=await response.json().catch(()=>undefined) as {access_token?:unknown}|undefined;
 const token=response.ok&&typeof session?.access_token==='string'?session.access_token:'';
 if(!token)throw new Refusal('Sign-in failed. Check the email and password.',EXIT.failed);
 const api:Api=async(path,method='GET',body)=>{
  const answer=await deps.fetch(base+'/management/v1'+path,{method,redirect:'error',
   headers:{authorization:'Bearer '+token,...(body===undefined?{}:{'content-type':'application/json'})},
   ...(body===undefined?{}:{body:JSON.stringify(body)})});
  const data=await answer.json().catch(()=>undefined) as {message?:unknown}|undefined;
  if(answer.ok)return data;
  const message=typeof data?.message==='string'?data.message:'';
  if(answer.status===401)throw new Refusal('The management session was not accepted. Sign in again.',EXIT.failed);
  if(answer.status===403)throw new Refusal('Your account has no permission for this action.',EXIT.failed);
  if(answer.status===409)throw new Refusal(CONFLICTS[message]??'The request conflicts with the current state.',EXIT.failed);
  if(answer.status===400&&message==='Invalid share')throw new Refusal('The share must be a whole number from 1 to the ceiling.',EXIT.failed);
  if(answer.status===404&&message==='Active key not found')throw new Refusal('That key is not active.',EXIT.failed);
  throw new Refusal(`The management API answered ${answer.status}.`,EXIT.failed);
 };
 try {return await work(api);}
 finally {
  await deps.fetch(base+'/management/auth/v1/logout',{method:'POST',redirect:'error',
   headers:{apikey:managementPublishableKey,authorization:'Bearer '+token}}).then(reply=>reply.body?.cancel(),()=>{});
 }
}

type Project={id:string;name:string;client:string};

async function findProject(api:Api,reference:string):Promise<Project> {
 const organizations=(await api('/organizations')).data as {id:string;name:string}[];
 const projects:Project[]=[];
 for(const organization of organizations)
  for(const project of (await api(`/organizations/${organization.id}/projects`)).data as {id:string;name:string}[])
   projects.push({id:project.id,name:project.name,client:organization.name});
 const [first,second]=reference.split('/');
 const matches=projects.filter(project=>second===undefined?project.id===reference||project.name===reference
  :project.client===first&&project.name===second);
 if(matches.length===1)return matches[0]!;
 if(matches.length>1)throw new Refusal(`${reference} names more than one project; use its id: ${matches.map(project=>project.id).join(', ')}`);
 throw new Refusal(`No project ${reference} that your account can see`);
}

async function addEnvironment(deps:Deps,parsed:Parsed) {
 arity(parsed,2,2);
 const [projectReference,name]=parsed.args as [string,string];
 return signedIn(deps,parsed.flags,async api=>{
  const project=await findProject(api,projectReference);
  const created=await api(`/projects/${project.id}/environments`,'POST',{name});
  deps.out(`Environment ${project.client}/${project.name}/${name} is ${created.state} as ${created.id}.`);
  deps.out('The worker provisions it now; follow it with: sbarbase environments');
  return EXIT.ok;
 });
}

type Key={id:string;kind:string;created_at:number;revoked_at:number|null};

async function rotateKey(deps:Deps,parsed:Parsed) {
 arity(parsed,1,1);
 const environment=environmentId(deps,parsed.args[0]!);
 const chosen=parsed.flags['--revoke'];
 return signedIn(deps,parsed.flags,async api=>{
  const active=((await api(`/environments/${environment}/keys`)).data as Key[])
   .filter(key=>key.kind==='publishable'&&key.revoked_at===null);
  let old:Key|undefined;
  if(typeof chosen==='string') {
   old=active.find(key=>key.id===chosen||key.id.startsWith(chosen));
   if(!old||active.filter(key=>key.id.startsWith(chosen)).length>1)throw new Refusal(`${chosen} is not exactly one active key of this environment`);
  } else if(active.length>1) {
   throw new Refusal('This environment has more than one active key, and revoking all of them would break other apps. '+
    'Name the one to replace with --revoke:\n'+active.map(key=>`  ${key.id}  created ${new Date(key.created_at).toISOString()}`).join('\n'));
  } else old=active[0];
  const issued=await api(`/environments/${environment}/keys`,'POST') as {id:string;token:string};
  deps.err(`New publishable key ${issued.id}. Save it now: it is shown only once.`);
  deps.out(issued.token);
  if(!old){deps.err('There was no active key to revoke.');return EXIT.ok;}
  try {await api(`/environments/${environment}/keys/${old.id}`,'DELETE');}
  catch(error) {
   deps.err(`The new key is active, but revoking the old key ${old.id} failed: ${(error as Error).message} `+
    'The old key is still active; revoke it from the console.');
   return EXIT.failed;
  }
  deps.err(`Revoked the old key ${old.id}. Apps that still use it now get 401.`);
  return EXIT.ok;
 });
}

async function studio(deps:Deps,parsed:Parsed) {
 arity(parsed,2,2);
 const action=parsed.args[0];
 if(action!=='start'&&action!=='stop')throw new Usage('studio takes start or stop');
 const environment=environmentId(deps,parsed.args[1]!);
 return signedIn(deps,parsed.flags,async api=>{
  await api(`/environments/${environment}/studio`,action==='start'?'POST':'DELETE');
  deps.out(action==='start'?'Studio requested. The supervisor starts it within a few seconds; open it from the console.'
   :'Studio stop requested. The supervisor stops it and closes its database login.');
  return EXIT.ok;
 });
}

type Share={share:number;default:number;ceiling:number;operator:boolean;total?:number;allocated?:number};

async function share(deps:Deps,parsed:Parsed) {
 arity(parsed,1,2);
 const environment=environmentId(deps,parsed.args[0]!);
 const wanted=parsed.args[1];
 if(wanted!==undefined&&!/^[1-9]\d{0,5}$/.test(wanted))throw new Usage('<n> must be a positive whole number');
 return signedIn(deps,parsed.flags,async api=>{
  const path=`/environments/${environment}/share`;
  const state=(wanted===undefined?await api(path):await api(path,'PUT',{share:Number(wanted)})).data as Share;
  if(wanted!==undefined)deps.out(`Share set to ${state.share}. The gateway applies it to the next request.`);
  deps.out(`Share ${state.share} (default ${state.default}, ceiling ${state.ceiling})`);
  if(state.operator&&state.total!==undefined)deps.out(`Allocated ${state.allocated} of ${state.total} across every ready environment`);
  return EXIT.ok;
 });
}

// ---- Delegations -----------------------------------------------------------------------

/** The number of backups the daily run keeps: this shell's SBARBASE_BACKUP_KEEP, or on a
 * systemd install the unit's, which a shell under sudo does not inherit. */
async function dailyKeep(deps:Deps):Promise<string|undefined> {
 if(deps.env.SBARBASE_BACKUP_KEEP)return positiveInteger(deps.env.SBARBASE_BACKUP_KEEP,'SBARBASE_BACKUP_KEEP');
 if(deps.inContainer||!existsSync(deps.unitPath))return undefined;
 let shown:{code:number;stdout:string};
 try {shown=await deps.capture(['systemctl','show','--property','Environment',UNIT]);} catch {return undefined;}
 const value=shown.code===0?shown.stdout.match(/(?:^Environment=|[\s"])SBARBASE_BACKUP_KEEP=([^\s"]*)/m)?.[1]:undefined;
 return value?positiveInteger(value,'SBARBASE_BACKUP_KEEP in the unit'):undefined;
}

async function backup(deps:Deps,parsed:Parsed) {
 if(parsed.args[0]!=='now')throw new Usage('Use: sbarbase backup now [<environment>]');
 arity(parsed,1,2);
 const target=parsed.args[1]===undefined||parsed.args[1]==='all'?'all':backupTarget(deps,parsed.args[1]);
 const keep=positiveInteger(parsed.flags['--keep'],'--keep')??await dailyKeep(deps);
 if(!keep)deps.err('SBARBASE_BACKUP_KEEP is not set here, so lab/backup.py keeps its default of 7 backups per environment.');
 return deps.run([PYTHON,'lab/backup.py','create',target,...(keep?['--keep',keep]:[])]);
}

async function backups(deps:Deps,parsed:Parsed) {
 if(parsed.args[0]!=='list')throw new Usage('Use: sbarbase backups list [<environment>]');
 arity(parsed,1,2);
 return deps.run([PYTHON,'lab/backup.py','list',...(parsed.args[1]?[backupTarget(deps,parsed.args[1])]:[])]);
}

async function restore(deps:Deps,parsed:Parsed) {
 arity(parsed,2,2);
 const [reference,name]=parsed.args as [string,string];
 const environment=backupTarget(deps,reference);
 if(!STAMP.test(name))throw new Usage('<backup> is the time sbarbase backups list shows, such as 20260924T030000Z');
 if(!parsed.flags['--yes']) {
  if(!deps.isTTY)throw new Refusal('Restore replaces data, so it needs confirmation: run it at a terminal or pass --yes');
  deps.err(`Restore ${reference} from backup ${name}.`);
  deps.err('Rows, users and files written to this environment after that backup will be gone.');
  deps.err('Its Auth and REST pause during the restore; every other environment keeps serving.');
  const answer=(await deps.prompt(`Type the backup time (${name}) to continue: `)).trim();
  if(answer!==name){deps.err('Not confirmed; nothing was changed.');return EXIT.refused;}
 }
 return deps.run([PYTHON,'lab/backup.py','restore',environment,name]);
}

async function upgrade(deps:Deps,parsed:Parsed) {
 arity(parsed,0,1);
 const action=parsed.args[0]??'check';
 if(!['check','start','status','rollback'].includes(action))throw new Usage('upgrade takes check, start, status or rollback');
 const to=parsed.flags['--to'];
 if(to!==undefined&&!['check','start'].includes(action))throw new Usage('--to applies to check and start');
 return deps.run([PYTHON,'lab/upgrade.py',action,...(typeof to==='string'?['--to',to]:[])]);
}

async function logs(deps:Deps,parsed:Parsed) {
 arity(parsed,0,2);
 const [unit='supervisor',reference]=parsed.args;
 const lines=positiveInteger(parsed.flags['--lines'],'--lines')??'100';
 const follow=parsed.flags['--follow']?['--follow']:[];
 if(unit==='supervisor') {
  if(reference)throw new Usage('The supervisor log is not per environment');
  if(deps.inContainer)throw new Refusal('Inside the container the supervisor log is its output: on the host, run docker compose logs sbarbase',EXIT.usage);
  if(!existsSync(deps.unitPath))throw new Refusal(`No ${UNIT} is installed; a hand-started lab/dev.py logs to its own terminal`,EXIT.usage);
  return deps.run(['journalctl','--unit',UNIT,'--lines',lines,'--no-pager',...follow]);
 }
 let container:string;
 if(unit==='auth'||unit==='rest') {
  if(!reference)throw new Usage(`${unit} logs are per environment: sbarbase logs ${unit} <environment>`);
  container=`${PREFIX}-${runtimeOf(deps,reference)}-${unit}`;
 } else if(unit==='storage'||unit==='database') {
  if(reference)throw new Usage(`${unit} is shared by every environment; name no environment`);
  container=unit==='storage'?`${PREFIX}-storage`:`${PREFIX}-db`;
 } else throw new Usage('logs takes supervisor, auth, rest, storage or database');
 return deps.run(['docker','logs','--tail',lines,...follow,container]);
}

export async function main(argv:string[],deps:Deps):Promise<number> {
 let parsed:Parsed;
 try {parsed=parse(argv);}
 catch(error) {
  deps.err(`sbarbase: ${(error as Error).message}. Run sbarbase help.`);
  return error instanceof Refusal?error.code:EXIT.usage;
 }
 if(parsed.command==='help'||parsed.flags['--help']) {
  const topic=parsed.command==='help'?parsed.args[0]:parsed.command;
  if(topic&&!COMMAND_HELP[topic]&&topic!=='help'){deps.err(`sbarbase: no help for ${topic}`);return EXIT.usage;}
  deps.out(topic&&COMMAND_HELP[topic]?COMMAND_HELP[topic]:HELP);
  return EXIT.ok;
 }
 try {
  switch(parsed.command) {
   case 'status':arity(parsed,0,0);return await statusCommand(deps,!!parsed.flags['--json']);
   case 'environments':arity(parsed,0,0);return await environmentsCommand(deps,!!parsed.flags['--json']);
   case 'backup':return await backup(deps,parsed);
   case 'backups':return await backups(deps,parsed);
   case 'restore':return await restore(deps,parsed);
   case 'add-environment':return await addEnvironment(deps,parsed);
   case 'rotate-key':return await rotateKey(deps,parsed);
   case 'upgrade':return await upgrade(deps,parsed);
   case 'studio':return await studio(deps,parsed);
   case 'share':return await share(deps,parsed);
   case 'logs':return await logs(deps,parsed);
  }
  return EXIT.usage;
 } catch(error) {
  if(error instanceof Refusal){deps.err(`sbarbase: ${error.message}`);return error.code;}
  // Anything unexpected is reported by kind only, never with a response body or a secret.
  deps.err('sbarbase: the operation failed unexpectedly');
  return EXIT.failed;
 }
}

// ---- The real process ----------------------------------------------------------------

let lines:AsyncIterator<string>|undefined;
async function readLine():Promise<string> {
 lines??=(console as unknown as AsyncIterable<string>)[Symbol.asyncIterator]();
 const next=await lines.next();
 return next.done?'':next.value;
}

function echo(on:boolean) {
 Bun.spawnSync(['stty',on?'echo':'-echo'],{stdin:'inherit',stdout:'ignore',stderr:'ignore'});
}

export function processDeps(root=resolve(import.meta.dir,'..')):Deps {
 return {
  root,env:process.env,
  run:async argv=>{
   const child=Bun.spawn(argv,{cwd:root,stdin:'inherit',stdout:'inherit',stderr:'inherit'});
   // Ctrl+C reaches the child from the terminal; waiting for it lets the tool clean up and report.
   const wait=()=>{};
   process.on('SIGINT',wait);
   try {return await child.exited;} finally {process.off('SIGINT',wait);}
  },
  capture:async argv=>{
   const child=Bun.spawn(argv,{cwd:root,stdin:'ignore',stdout:'pipe',stderr:'ignore'});
   return {stdout:await new Response(child.stdout).text(),code:await child.exited};
  },
  fetch,
  prompt:async(question,secret=false)=>{
   process.stderr.write(question);
   if(!secret)return readLine();
   echo(false);
   try {return await readLine();} finally {echo(true);process.stderr.write('\n');}
  },
  stdin:()=>Bun.stdin.text(),
  isTTY:!!process.stdin.isTTY,
  out:text=>process.stdout.write(text+'\n'),
  err:text=>process.stderr.write(text+'\n'),
  pidAlive:pid=>{
   try {process.kill(pid,0);return true;}
   catch(error) {return (error as {code?:string}).code==='EPERM';}
  },
  inContainer:existsSync('/.dockerenv'),
  unitPath:'/etc/systemd/system/'+UNIT,
 };
}

if(import.meta.main) {
 const deps=processDeps();
 process.chdir(deps.root);
 process.exit(await main(process.argv.slice(2),deps));
}
