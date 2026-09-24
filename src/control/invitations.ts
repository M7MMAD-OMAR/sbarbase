import {Catalog,type MembershipRole} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';

/** The management realm's account operations, done on the server with its admin API. The
 * service role token stays inside the implementation (lab/upstream-app.ts); none of these
 * answers ever carries it. docs/engineering/INVITATIONS.md */
export type InvitationAccounts={
 /** Creates a confirmed account, or says one already exists for that email, or that the
  * realm refused the password. */
 create(email:string,password:string):Promise<{id:string}|'exists'|'weak'>;
 /** The id and email behind a session token, or null. */
 session(token:string):Promise<{id:string;email:string}|null>;
};

const INVALID={message:'This invitation is not valid'};
const MIN_PASSWORD=12,MAX_PASSWORD=256,FAILURES_PER_MINUTE=20,MAX_BODY=8192;

/** A small JSON object body, read with a size bound: the redeem route answers anyone. */
async function json(request:Request):Promise<Record<string,unknown>|null> {
 const declared=Number(request.headers.get('content-length')??'0');
 if(!Number.isFinite(declared)||declared>MAX_BODY||!request.body)return null;
 const reader=request.body.getReader(),chunks:Uint8Array[]=[];let size=0;
 try {
  while(true){const part=await reader.read();if(part.done)break;size+=part.value.length;
   if(size>MAX_BODY){void reader.cancel().catch(()=>{});return null;}chunks.push(part.value);}
  const value=JSON.parse(Buffer.concat(chunks).toString('utf8'));
  return value&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:null;
 } catch{return null;}
}

/** Owners and admins invite, list and cancel under `/management/v1/organizations/{id}/invitations`;
 * anyone holding a token redeems it at `/management/invitations/redeem`. */
export function invitationHandler(catalog:Catalog,identify:ManagementIdentity,accounts?:InvitationAccounts,now=Date.now) {
 let window=0,failures=0;
 const limited=()=>{const minute=Math.floor(now()/60_000);if(minute!==window){window=minute;failures=0;}return failures>=FAILURES_PER_MINUTE;};
 const failed=()=>{failures++;return reply(400,INVALID);};
 return async(request:Request):Promise<Response>=>{
  const path=new URL(request.url).pathname;
  if(path==='/management/invitations/redeem') {
   if(request.method!=='POST')return reply(405,{message:'Method not allowed'});
   if(!accounts)return reply(503,{message:'Invitations are unavailable'});
   const input=await json(request);
   const invitation=typeof input?.token==='string'?catalog.invitationFor(input.token):undefined;
   // Only failures are limited, so noise from anyone never blocks a valid invitation.
   if(!invitation)return limited()?reply(429,{message:'Too many attempts. Wait a minute and try again.'}):failed();
   if(input?.preview===true){const offer=catalog.invitationPreview(invitation.id);
    return offer?reply(200,{data:{...offer,email:invitation.email}}):failed();}
   try {
    const header=request.headers.get('authorization');
    if(header&&/^Bearer \S+$/i.test(header)&&header.length<=8192) {
     // Already signed in: the session's email must be the invited one.
     const user=await accounts.session(header.slice(7));
     if(!user||user.email.toLowerCase()!==invitation.email)return failed();
     const joined=catalog.acceptInvitation(invitation.id,user.id);
     return reply(200,{data:{organization:joined.organization,role:joined.role,email:invitation.email}});
    }
    const password=input?.password;
    if(typeof password!=='string'||password.length<MIN_PASSWORD||password.length>MAX_PASSWORD)
     return reply(400,{message:`Choose a password of at least ${MIN_PASSWORD} characters`});
    const created=await accounts.create(invitation.email,password);
    if(created==='weak')return reply(400,{message:'Choose a stronger password'});
    if(created==='exists')return reply(409,{message:'An account exists for this email. Sign in, then open the invitation link again.',email:invitation.email});
    const joined=catalog.acceptInvitation(invitation.id,created.id);
    return reply(201,{data:{organization:joined.organization,role:joined.role,email:invitation.email}});
   } catch(error) {
    if(error instanceof Error&&error.message==='Invalid invitation')return failed();
    return reply(500,{message:'Management operation failed'});
   }
  }
  const match=path.match(/^\/management\/v1\/organizations\/([a-f0-9-]{36})\/invitations(?:\/([a-f0-9-]{36}))?$/);
  if(!match)return reply(404,{message:'Unknown route'});
  const [,organization,id]=match;
  if(!(id?['DELETE']:['GET','POST']).includes(request.method))return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  try {
   if(id){catalog.cancelInvitation(actor,organization!,id);return reply(200,{cancelled:true});}
   if(request.method==='GET')return reply(200,{data:catalog.listInvitations(actor,organization!)});
   const input=await json(request);
   if(!input||Object.keys(input).some(key=>!['email','role'].includes(key))||typeof input.email!=='string'||typeof input.role!=='string')
    return reply(400,{message:'Invalid request'});
   const created=catalog.createInvitation(actor,organization!,input.email,input.role as MembershipRole);
   return reply(201,{data:created});
  } catch(error) {
   const message=error instanceof Error?error.message:'';
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(['Invalid email','Invalid role'].includes(message))return reply(400,{message});
   if(message==='Unknown invitation')return reply(404,{message});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
