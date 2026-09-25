import {Catalog,type MembershipRole,type Ownership} from './catalog';
import type {KeyStore} from './keys';
import {authenticate,reply,type ManagementIdentity} from './auth';
import {readJsonCached} from '../http/cached-json';
import {join} from 'node:path';
import {UPDATES_DIRECTORY,UpdateRefusal,requestUpdate,saveSettings,serverZone,updatesView,validateSettings,type UpdateRequestKind} from './updates';

/** Runtime state directory, the same tree the runtime writes the catalog in. */
const MAIL_STATE_DIRECTORY='.lab/upstream';
const MAIL_STATE_FILE='mail-state.json';
/** The non secret fields lab/mail_config.py `summarize` writes, minus `user` and `pass`.
 * The summary carries those two as the literal markers `set`/`empty`; this route exposes no
 * credential field at all, not even a marker, so they are dropped by name. */
const MAIL_SUMMARY_FIELDS=['host','port','admin_email','sender_name','reply_to','max_frequency','otp_exp',
  'otp_length','secure_email_change','autoconfirm','rate_limit_email_sent','rate_limit_otp',
  'rate_limit_verify','rate_limit_header'] as const;
/** The notification design: owner and admin only, most recent events. */
const NOTIFICATION_ROLES:MembershipRole[]=['owner','admin'];
const NOTIFICATION_EVENT_LIMIT=50;

/** Missing file: nothing was reconciled yet, so every environment is unconfigured.
 * A file that exists and cannot be read is a failure, never a silent unconfigured. */
function mailEntries(directory:string):Record<string,unknown> {
  let parsed:unknown;
  try {parsed=readJsonCached(join(directory,MAIL_STATE_FILE));}
  catch(error) {
    if((error as {code?:string}).code==='ENOENT')return {};
    throw error;
  }
  if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw new Error('Invalid mail state');
  return parsed as Record<string,unknown>;
}

/** One environment's own recorded entry, or the unconfigured state when it has none.
 * Only fields the recorded summary has are copied: this route invents no value. */
function mailEntry(entries:Record<string,unknown>,runtime:string) {
  const recorded=entries[runtime];
  if(!recorded||typeof recorded!=='object'||Array.isArray(recorded))return {state:'unconfigured'};
  const source=recorded as Record<string,unknown>,entry:Record<string,unknown>={};
  if(typeof source.state==='string')entry.state=source.state;
  if(source.credentials==='set'||source.credentials==='none')entry.credentials=source.credentials;
  if(typeof source.at==='number')entry.at=source.at;
  for(const field of MAIL_SUMMARY_FIELDS)if(field in source)entry[field]=source[field];
  if(!('state' in entry))entry.state='unconfigured';
  return entry;
}

/** Both halves of the notification state come from one existing catalog read, so no second
 * query touches these rows. An event counts as undelivered while any of its channels has
 * not reached delivered. The rows carry no recipient and no detail payload. */
function notificationState(catalog:Catalog,actor:string) {
  const events=catalog.listNotifications(NOTIFICATION_EVENT_LIMIT,actor);
  const undelivered=new Set(events.filter(event=>event.state!=='delivered').map(event=>event.id)).size;
  return {undelivered,events};
}

class InputError extends Error {}
/** A JSON object with exactly the allowed keys (plus any of the optional ones), at most 4 KiB,
 * read within five seconds. */
async function body(request:Request):Promise<{name:string}>;
async function body(request:Request,allowed:string[],optional?:string[]):Promise<Record<string,unknown>>;
async function body(request:Request,allowed:string[]=['name'],optional:string[]=[]):Promise<Record<string,unknown>> {
  if(request.headers.get('content-type')?.split(';')[0]?.trim()!=='application/json'||!request.body)
    throw new InputError();
  const reader=request.body.getReader(),chunks:Uint8Array[]=[];let bytes=0;
  let timer:ReturnType<typeof setTimeout>|undefined;
  const expired=new Promise<never>((_,reject)=>{timer=setTimeout(()=>{
    reject(new InputError());void reader.cancel().catch(()=>{});
  },5000);});
  try {
    while(true) {
      const chunk=await Promise.race([reader.read(),expired]);
      if(chunk.done) break;
      bytes+=chunk.value.length;
      if(bytes>4096) {void reader.cancel().catch(()=>{});throw new InputError();}
      chunks.push(chunk.value);
    }
    const parsed=JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if(!parsed||typeof parsed!=='object'||Array.isArray(parsed)||Object.keys(parsed).some(k=>!allowed.includes(k)&&!optional.includes(k))||
      allowed.some(k=>!(k in parsed)))
      throw new InputError();
    if(allowed.includes('name')&&typeof parsed.name!=='string')throw new InputError();
    return parsed;
  } catch {throw new InputError();}
  finally {clearTimeout(timer);reader.releaseLock();}
}

/** The refusals these routes answer with 409 and their fixed message: the request conflicts
 * with the current state, and nothing was changed. */
const CONFLICTS=new Set(['Name already used','Environment capacity reached','Organization has projects',
  'Project has environments','Installation organization cannot be deleted','Provisioning is active',
  'Environment services are still on','Runtime was deleted here','Organization name belongs to another organization',
  'Project belongs to another organization','Environment exists with another project or runtime',
  'Runtime belongs to another environment']);
const INVALID=new Set(['Invalid name','Invalid ownership','Invalid runtime']);
function refusal(error:unknown):Response {
  const message=error instanceof Error?error.message:'';
  if(error instanceof UpdateRefusal)return reply(error.status,{message});
  if(error instanceof InputError||INVALID.has(message))return reply(400,{message:'Invalid request'});
  if(message==='Forbidden')return reply(403,{message:'Forbidden'});
  if(CONFLICTS.has(message))return reply(409,{message});
  return reply(500,{message:'Management operation failed'});
}

/** Metadata API. Creating an organization, re-linking a restored environment and the updates
 * routes are limited to installation operators. Moving a project and deleting an environment
 * revoke the API keys of the runtimes involved, so both need the key store and refuse without it.
 * `updatesDirectory` and `checkout` place the update channel's files (src/control/updates.ts).
 */
export function managementHandler(catalog:Catalog,identify:ManagementIdentity,mailDirectory=MAIL_STATE_DIRECTORY,keys?:KeyStore,
  updatesDirectory=UPDATES_DIRECTORY,checkout='.') {
  return async(request:Request):Promise<Response>=>{
    const path=new URL(request.url).pathname;
    const item=path.match(/^\/management\/v1\/(organizations|projects|environments)\/([a-f0-9-]{36})$/);
    if(item) {
      if(!['PATCH','DELETE'].includes(request.method))return reply(405,{message:'Method not allowed'});
      const actor=await authenticate(identify,request);
      if(actor instanceof Response)return actor;
      const kind=item[1]!,id=item[2]!;
      try {
        if(request.method==='PATCH') {
          const {name}=await body(request);
          if(kind==='organizations')catalog.renameOrganization(actor,id,name);
          else if(kind==='projects')catalog.renameProject(actor,id,name);
          else catalog.renameEnvironment(actor,id,name);
          return reply(200,{id,name:name.trim()});
        }
        if(request.body)return reply(400,{message:'Invalid request'});
        if(kind==='organizations'){catalog.deleteOrganization(actor,id);return reply(200,{deleted:true});}
        if(kind==='projects'){catalog.deleteProject(actor,id);return reply(200,{deleted:true});}
        if(!keys)return reply(500,{message:'Key revocation unavailable'});
        const {runtime}=catalog.deleteEnvironment(actor,id);
        // The catalog commits first, so a refusal changes nothing. From then on the gateway
        // answers 401 for the runtime whatever happens to the keys; revoking them keeps it so.
        try {return reply(200,{deleted:true,revoked:runtime?keys.revokeAll(runtime):0});}
        catch {return reply(500,{message:'Environment deleted; revoking its keys failed'});}
      } catch(error) {return refusal(error);}
    }
    const move=path.match(/^\/management\/v1\/projects\/([a-f0-9-]{36})\/move$/);
    if(move) {
      if(request.method!=='POST')return reply(405,{message:'Method not allowed'});
      const actor=await authenticate(identify,request);
      if(actor instanceof Response)return actor;
      if(!keys)return reply(500,{message:'Key revocation unavailable'});
      try {
        const input=await body(request,['organization']);
        if(typeof input.organization!=='string'||!/^[a-f0-9-]{36}$/.test(input.organization))return reply(400,{message:'Invalid request'});
        const moved=catalog.transferProject(actor,move[1]!,input.organization);
        // People of the source organization may hold the keys, so none of them survives the move.
        // The move is committed first, so a name clash in the destination costs nobody a key.
        let revoked=0;
        try {for(const runtime of moved.runtimes)revoked+=keys.revokeAll(runtime);}
        catch {return reply(500,{message:'Project moved; revoking its keys failed'});}
        return reply(200,{organization:input.organization,cancelled:moved.cancelled,revoked});
      } catch(error) {return refusal(error);}
    }
    if(path==='/management/v1/relink') {
      if(request.method!=='POST')return reply(405,{message:'Method not allowed'});
      const actor=await authenticate(identify,request);
      if(actor instanceof Response)return actor;
      try {
        const input=await body(request,['runtime','ownership']);
        return reply(200,{data:catalog.relinkEnvironment(actor,input.ownership as Ownership,input.runtime as string)});
      } catch(error) {return refusal(error);}
    }
    if(path==='/management/v1/organizations') {
      if(!['GET','POST'].includes(request.method))return reply(405,{message:'Method not allowed'});
      const actor=await authenticate(identify,request);
      if(actor instanceof Response)return actor;
      try {
        if(request.method==='GET')
          return reply(200,{data:catalog.listOrganizations(actor),operator:catalog.installationOperator(actor)});
        // A new client is an installation decision, so only the bootstrap organization's
        // owners and admins make it; the creator becomes its owner.
        if(!catalog.installationOperator(actor))return reply(403,{message:'Forbidden'});
        const input=await body(request);
        return reply(201,{id:catalog.createOrganization(actor,input.name)});
      } catch(error) {
        if(error instanceof InputError||error instanceof Error&&error.message==='Invalid name')
          return reply(400,{message:'Invalid request'});
        return reply(500,{message:'Management operation failed'});
      }
    }
    const member=path.match(/^\/management\/v1\/organizations\/([a-f0-9-]{36})\/members\/([A-Za-z0-9._@:+-]{1,200})$/);
    if(member) {
      if(!['PUT','DELETE'].includes(request.method))return reply(405,{message:'Method not allowed'});
      const actor=await authenticate(identify,request);
      if(actor instanceof Response)return actor;
      try {
        let role:MembershipRole|null=null;
        if(request.method==='PUT') {
          let input:unknown;
          try{input=await request.json();}catch{return reply(400,{message:'Invalid request'});}
          const value=input&&typeof input==='object'&&!Array.isArray(input)&&Object.keys(input).length===1?(input as {role?:unknown}).role:undefined;
          if(typeof value!=='string'||!['owner','admin','viewer'].includes(value))return reply(400,{message:'Invalid request'});
          role=value as MembershipRole;
        }
        return reply(200,{data:catalog.changeMember(actor,member[1]!,decodeURIComponent(member[2]!),role)});
      } catch(error) {
        const message=error instanceof Error?error.message:'';
        if(message==='Forbidden')return reply(403,{message:'Forbidden'});
        if(message==='Unknown member')return reply(404,{message:'Unknown member'});
        if(message==='Last owner cannot be removed')return reply(409,{message});
        return reply(500,{message:'Management operation failed'});
      }
    }
    const members=path.match(/^\/management\/v1\/organizations\/([a-f0-9-]{36})\/members$/);
    if(members) {
      if(request.method!=='GET')return reply(405,{message:'Method not allowed'});
      const actor=await authenticate(identify,request);
      if(actor instanceof Response)return actor;
      try {return reply(200,{data:catalog.listMembers(actor,members[1]!)});}
      catch(error) {
        if(error instanceof Error&&error.message==='Forbidden')return reply(403,{message:'Forbidden'});
        return reply(500,{message:'Management operation failed'});
      }
    }
    const audit=path.match(/^\/management\/v1\/organizations\/([a-f0-9-]{36})\/audit$/);
    if(audit) {
      if(request.method!=='GET')return reply(405,{message:'Method not allowed'});
      const actor=await authenticate(identify,request);
      if(actor instanceof Response)return actor;
      try {return reply(200,{data:catalog.auditEvents(actor,audit[1]!)});}
      catch(error) {
        if(error instanceof Error&&error.message==='Forbidden')return reply(403,{message:'Forbidden'});
        return reply(500,{message:'Management operation failed'});
      }
    }
    const retry=path.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/retry$/);
    if(retry) {
      if(request.method!=='POST')return reply(405,{message:'Method not allowed'});
      if(request.body)return reply(400,{message:'Invalid request'});
      const actor=await authenticate(identify,request);
      if(actor instanceof Response)return actor;
      try {catalog.retryProvision(actor,retry[1]!);return reply(202,{state:'queued'});}
      catch(error) {
        if(error instanceof Error&&error.message==='Forbidden')return reply(403,{message:'Forbidden'});
        if(error instanceof Error&&['Operation is not retryable','Environment capacity reached'].includes(error.message))
          return reply(409,{message:error.message});
        return reply(500,{message:'Management operation failed'});
      }
    }
    const update=path.match(/^\/management\/v1\/updates(\/check|\/apply|\/rollback|\/settings)?$/);
    if(update) {
      // The installation's own version: only its operators (the bootstrap organization's owners
      // and admins) see or change it. The supervisor carries out what is asked (lab/updates.py).
      const action=update[1]??'';
      if(request.method!==(action===''?'GET':action==='/settings'?'PUT':'POST'))return reply(405,{message:'Method not allowed'});
      const actor=await authenticate(identify,request);
      if(actor instanceof Response)return actor;
      try {
        if(!catalog.installationOperator(actor))throw new Error('Forbidden');
        if(action==='')return reply(200,{data:updatesView(updatesDirectory,checkout)});
        if(action==='/settings') {
          const settings=validateSettings(await body(request,['check','automatic','window']));
          if(typeof settings==='string')throw new UpdateRefusal(settings,400);
          // The window is read in the supervisor's time zone, returned beside the settings.
          return reply(200,{data:saveSettings(settings,updatesDirectory),timezone:serverZone(updatesDirectory)});
        }
        let version:string|undefined,acknowledged=false;
        if(action==='/apply') {
          const input=await body(request,['version'],['acknowledged']);
          if(typeof input.version!=='string'||!/^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$/.test(input.version))
            throw new UpdateRefusal('Name the release version to install.',400);
          // `acknowledged: true` confirms the warning of a release that migrates environment databases.
          if(input.acknowledged!==undefined&&typeof input.acknowledged!=='boolean')throw new InputError();
          version=input.version;acknowledged=input.acknowledged===true;
        } else if(request.body)throw new InputError();
        requestUpdate(action.slice(1) as UpdateRequestKind,version,updatesDirectory,acknowledged);
        return reply(202,{state:'requested'});
      } catch(error) {return refusal(error);}
    }
    if(path==='/management/v1/notifications') {
      if(request.method!=='GET')return reply(405,{message:'Method not allowed'});
      const actor=await authenticate(identify,request);
      if(actor instanceof Response)return actor;
      try {
        // Owners and admins only; the catalog then returns just the events of their own
        // organizations, so one client never reads another client's ids or failure reasons.
        if(!catalog.listOrganizations(actor).some(membership=>NOTIFICATION_ROLES.includes(membership.role)))
          return reply(403,{message:'Forbidden'});
        return reply(200,{data:notificationState(catalog,actor)});
      } catch {return reply(500,{message:'Management operation failed'});}
    }
    const mail=path.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/mail$/);
    if(mail) {
      if(request.method!=='GET')return reply(405,{message:'Method not allowed'});
      const actor=await authenticate(identify,request);
      if(actor instanceof Response)return actor;
      const id=mail[1];if(!id) return reply(404,{message:'Unknown route'});
      try {
        // The neighbouring environment routes read the same catalog row to reach the runtime
        // identifier, so this route maps the environment the same way and inherits the same
        // membership check, the same Forbidden answer for an unknown environment, and the
        // same unconfigured state for an environment that has no mail entry.
        const job=catalog.getProvision(actor,id);
        return reply(200,{data:mailEntry(mailEntries(mailDirectory),job.runtime)});
      } catch(error) {
        if(error instanceof Error&&error.message==='Forbidden')return reply(403,{message:'Forbidden'});
        return reply(500,{message:'Management operation failed'});
      }
    }
    const match=path.match(/^\/management\/v1\/(organizations|projects|environments)\/([a-f0-9-]{36})\/(projects|environments|provision)$/);
    if(!match||!((match[1]==='organizations'&&match[3]==='projects')||
      (match[1]==='projects'&&match[3]==='environments')||
      (match[1]==='environments'&&match[3]==='provision'))) return reply(404,{message:'Unknown route'});
    if(!['GET','POST'].includes(request.method)||(match[1]==='environments'&&request.method!=='GET')) return reply(405,{message:'Method not allowed'});
    const actor=await authenticate(identify,request);
    if(actor instanceof Response)return actor;
    const id=match[2];if(!id) return reply(404,{message:'Unknown route'});
    try {
      if(match[1]==='environments') {
        const job=catalog.getProvision(actor,id);
        return reply(200,{environment:job.environment,state:job.state,attempt:job.attempt,...(job.failure?{failure:job.failure}:{})});
      }
      if(request.method==='GET') return reply(200,{data:match[1]==='organizations'
        ?catalog.listProjects(actor,id):catalog.listEnvironments(actor,id)});
      const input=await body(request);
      const created=match[1]==='organizations'?catalog.createProject(actor,id,input.name)
        :catalog.createEnvironment(actor,id,input.name);
      return reply(match[1]==='organizations'?201:202,{id:created,state:match[1]==='organizations'?'metadata_only':'queued'});
    } catch(error) {
      if(error instanceof InputError||error instanceof Error&&error.message==='Invalid name')
        return reply(400,{message:'Invalid request'});
      if(error instanceof Error&&error.message==='Forbidden') return reply(403,{message:'Forbidden'});
      if(error instanceof Error&&error.message==='Name already used') return reply(409,{message:'Name already used'});
      if(error instanceof Error&&error.message==='Environment capacity reached') return reply(409,{message:'Environment capacity reached'});
      return reply(500,{message:'Management operation failed'});
    }
  };
}
