import {Catalog,type MembershipRole} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';
import {readJsonCached} from '../http/cached-json';
import {join} from 'node:path';

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
async function body(request:Request):Promise<{name:string}> {
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
    if(!parsed||Array.isArray(parsed)||Object.keys(parsed).some(k=>k!=='name')||typeof parsed.name!=='string')
      throw new InputError();
    return parsed;
  } catch {throw new InputError();}
  finally {clearTimeout(timer);reader.releaseLock();}
}

/** Metadata API only. Organization bootstrap and transfer intentionally remain
 * internal until invitations and complete runtime access revocation are ready.
 */
export function managementHandler(catalog:Catalog,identify:ManagementIdentity,mailDirectory=MAIL_STATE_DIRECTORY) {
  return async(request:Request):Promise<Response>=>{
    const path=new URL(request.url).pathname;
    if(path==='/management/v1/organizations') {
      if(request.method!=='GET')return reply(405,{message:'Method not allowed'});
      const actor=await authenticate(identify,request);
      if(actor instanceof Response)return actor;
      try{return reply(200,{data:catalog.listOrganizations(actor)});}
      catch{return reply(500,{message:'Management operation failed'});}
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
      return reply(500,{message:'Management operation failed'});
    }
  };
}
