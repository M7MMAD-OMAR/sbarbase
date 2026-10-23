import {Database} from 'bun:sqlite';
import {createHash,randomBytes,randomUUID} from 'node:crypto';
import {chmodSync} from 'node:fs';
import {hasSchema,migrateStore,refuseNewer} from './store-version';

/** Personal access tokens for agents and tools (docs/guides/agents.md).
 *
 * A token is `sbp_` and 40 hex characters, the format of a Supabase personal access token, which
 * the Supabase CLI checks for. It is shown once; only its SHA-256 digest is kept here, in its own
 * private file beside the API keys. A token acts as the person who made it, through the same
 * membership checks as the console, narrowed by what the token says:
 *
 * - its scope: every organization that person belongs to, one organization, or one project;
 * - read only, or read and write;
 * - an expiry of at most a year.
 *
 * It stops working when it expires, when it is revoked, when that person's password is reset or
 * an authenticator is recovered (their token epoch moves on), and when the installation's sign-in
 * policy changes (MFA required or not: the policy epoch moves on), so a token made under a weaker
 * policy does not outlive it. Signing out of the console, by hand or because a session expired,
 * ends sessions only and leaves tokens working. A membership removed or lowered applies at the
 * next call, because every call checks the current role. */

export const ACCESS_TOKEN=/^sbp_[a-f0-9]{40}$/;
export const TOKEN_NAME=/^[\p{L}\p{N} ._()'@:\/+-]{1,64}$/u;
export const MAX_TOKEN_DAYS=365;
/** Bumped with each step of the access token store ladder (src/control/store-version.ts). */
export const ACCESS_TOKEN_SCHEMA_VERSION=0;
const MAX_LIVE=50;
export type TokenScope={kind:'all'}|{kind:'organization';id:string}|{kind:'project';id:string};
export type AccessToken={id:string;name:string;actor:string;scope:TokenScope;read_only:boolean;created_at:number;expires_at:number;last_used_at:number|null;revoked_at:number|null};
type Row={id:string;name:string;actor:string;scope_kind:string;scope_id:string|null;read_only:number;created_at:number;expires_at:number;last_used_at:number|null;revoked_at:number|null;epoch:string};
const digest=(token:string)=>createHash('sha256').update(token).digest('hex');
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export class TokenRefusal extends Error {}

/** What a token is bound to: its owner's token epoch and the sign-in policy's epoch. */
export function tokenEpoch(security:{tokenEpoch(actor:string):number;policy():{epoch:number}}) {
 return (actor:string)=>`${security.tokenEpoch(actor)}.${security.policy().epoch}`;
}

function record(row:Row):AccessToken {
 const scope:TokenScope=row.scope_kind==='organization'?{kind:'organization',id:row.scope_id!}:row.scope_kind==='project'?{kind:'project',id:row.scope_id!}:{kind:'all'};
 return {id:row.id,name:row.name,actor:row.actor,scope,read_only:row.read_only===1,created_at:row.created_at,expires_at:row.expires_at,last_used_at:row.last_used_at,revoked_at:row.revoked_at};
}

export class AccessTokenStore {
 private db:Database;
 constructor(path:string,private readonly now:()=>number=Date.now) {
  this.db=new Database(path,{create:true,strict:true});
  try {
   this.db.exec('PRAGMA busy_timeout=5000;');
   refuseNewer(this.db,'Access token store',ACCESS_TOKEN_SCHEMA_VERSION);
   if(path!==':memory:')chmodSync(path,0o600);
   this.db.exec('PRAGMA journal_mode=DELETE;');
   this.migrate();
  } catch(error) {this.db.close();throw error;}
 }
 private migrate() {
  const ready=()=>hasSchema(this.db,{access_tokens:['epoch','revoked_at']},['access_tokens_actor']);
  migrateStore(this.db,'Access token store',ACCESS_TOKEN_SCHEMA_VERSION,ready,()=>this.db.exec(`CREATE TABLE IF NOT EXISTS access_tokens(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, actor TEXT NOT NULL, digest TEXT NOT NULL UNIQUE,
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('all','organization','project')), scope_id TEXT,
    read_only INTEGER NOT NULL CHECK(read_only IN (0,1)), epoch TEXT NOT NULL,
    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, last_used_at INTEGER, revoked_at INTEGER,
    CHECK((scope_kind='all')=(scope_id IS NULL))
   );
   CREATE INDEX IF NOT EXISTS access_tokens_actor ON access_tokens(actor);`));
 }

 /** A new token for `actor`, whose authority epoch is `epoch` now (tokenEpoch). The token is returned once. */
 issue(actor:string,epoch:string,input:{name:string;scope:TokenScope;read_only:boolean;days:number}):{token:string;data:AccessToken} {
  if(!actor||actor.length>200)throw new TokenRefusal('Invalid actor');
  if(!TOKEN_NAME.test(input.name))throw new TokenRefusal("A token name has 1 to 64 letters, digits, spaces and . _ ( ) ' @ : / + -");
  if(!Number.isInteger(input.days)||input.days<1||input.days>MAX_TOKEN_DAYS)throw new TokenRefusal(`A token lasts 1 to ${MAX_TOKEN_DAYS} days`);
  if(input.scope.kind!=='all'&&!UUID.test(input.scope.id))throw new TokenRefusal('Invalid scope');
  const now=this.now();
  const live=this.db.query<{n:number},[string,number]>('SELECT count(*) AS n FROM access_tokens WHERE actor=? AND revoked_at IS NULL AND expires_at>?').get(actor,now)!.n;
  if(live>=MAX_LIVE)throw new TokenRefusal(`You have ${MAX_LIVE} active tokens; revoke one first`);
  const token='sbp_'+randomBytes(20).toString('hex'),id=randomUUID();
  this.db.query(`INSERT INTO access_tokens(id,name,actor,digest,scope_kind,scope_id,read_only,epoch,created_at,expires_at,last_used_at,revoked_at)
   VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL)`).run(id,input.name,actor,digest(token),input.scope.kind,input.scope.kind==='all'?null:input.scope.id,
   input.read_only?1:0,epoch,now,now+input.days*86_400_000);
  return {token,data:this.get(actor,id)!};
 }

 /** The token's record when it is live and its owner's epoch is still `epoch(actor)`. Marks it used,
  * at most once a minute, so a busy agent does not write on every call. */
 resolve(token:string,epoch:(actor:string)=>string):AccessToken|null {
  if(!ACCESS_TOKEN.test(token))return null;
  const now=this.now();
  const row=this.db.query<Row,[string]>('SELECT * FROM access_tokens WHERE digest=?').get(digest(token));
  if(!row||row.revoked_at!==null||row.expires_at<=now||epoch(row.actor)!==row.epoch)return null;
  if(row.last_used_at===null||now-row.last_used_at>60_000){
   this.db.query('UPDATE access_tokens SET last_used_at=? WHERE id=?').run(now,row.id);row.last_used_at=now;
  }
  return record(row);
 }

 /** Whether a token resolved earlier still holds: not revoked, not expired, its owner's epoch
  * unchanged. Asked again before a change is published, as a console session is. */
 alive(id:string,epoch:(actor:string)=>string):boolean {
  const row=this.db.query<Row,[string]>('SELECT * FROM access_tokens WHERE id=?').get(id);
  return !!row&&row.revoked_at===null&&row.expires_at>this.now()&&epoch(row.actor)===row.epoch;
 }

 get(actor:string,id:string):AccessToken|undefined {
  const row=this.db.query<Row,[string,string]>('SELECT * FROM access_tokens WHERE actor=? AND id=?').get(actor,id);
  return row?record(row):undefined;
 }

 /** The actor's tokens, newest first; revoked and expired ones for 30 days, then not at all. */
 list(actor:string):AccessToken[] {
  const since=this.now()-30*86_400_000;
  return this.db.query<Row,[string,number,number]>(`SELECT * FROM access_tokens WHERE actor=? AND coalesce(revoked_at,expires_at)>? AND expires_at>?
   ORDER BY created_at DESC LIMIT 200`).all(actor,since,since).map(record);
 }

 revoke(actor:string,id:string):boolean {
  return this.db.query('UPDATE access_tokens SET revoked_at=? WHERE actor=? AND id=? AND revoked_at IS NULL').run(this.now(),actor,id).changes===1;
 }

 revokeAll(actor:string):number {
  return this.db.query('UPDATE access_tokens SET revoked_at=? WHERE actor=? AND revoked_at IS NULL').run(this.now(),actor).changes;
 }

 close(){this.db.close();}
}
