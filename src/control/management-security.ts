import type {Database} from 'bun:sqlite';
import {randomUUID} from 'node:crypto';

/** Durable admission and native MFA grants, shared by every controller using the catalog. */
export class ManagementSecurity {
 constructor(private db:Database){
  db.exec(`CREATE TABLE IF NOT EXISTS management_auth_policy(singleton INTEGER PRIMARY KEY CHECK(singleton=1),mfa_required INTEGER NOT NULL DEFAULT 1,epoch INTEGER NOT NULL DEFAULT 0);
   INSERT OR IGNORE INTO management_auth_policy(singleton) VALUES (1);
   CREATE TABLE IF NOT EXISTS management_password_grant(session TEXT PRIMARY KEY,actor TEXT NOT NULL,password INTEGER NOT NULL,epoch INTEGER NOT NULL,policy_epoch INTEGER NOT NULL,expires INTEGER NOT NULL);
   CREATE TABLE IF NOT EXISTS management_rate(
   bucket TEXT PRIMARY KEY,count INTEGER NOT NULL,expires INTEGER NOT NULL);
   CREATE TABLE IF NOT EXISTS management_mfa_epoch(actor TEXT PRIMARY KEY,epoch INTEGER NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
   CREATE TABLE IF NOT EXISTS management_mfa_grant(
   session TEXT PRIMARY KEY,actor TEXT NOT NULL,factor TEXT NOT NULL,verified INTEGER NOT NULL,
   epoch INTEGER NOT NULL,expires INTEGER NOT NULL);
   CREATE TABLE IF NOT EXISTS management_mfa_lease(actor TEXT PRIMARY KEY,nonce TEXT NOT NULL,expires INTEGER NOT NULL);
   CREATE TABLE IF NOT EXISTS management_mfa_recovery(receipt TEXT PRIMARY KEY,actor TEXT NOT NULL,target TEXT NOT NULL,
   state TEXT NOT NULL CHECK(state IN ('requested','completed','failed')),started INTEGER NOT NULL,finished INTEGER);`);
  if(!db.query<{name:string},[]>('PRAGMA table_info(management_mfa_epoch)').all().some(column=>column.name==='revoked'))
   db.exec('ALTER TABLE management_mfa_epoch ADD COLUMN revoked INTEGER NOT NULL DEFAULT 0');
 }
 policy():{mfa_required:boolean;epoch:number} {
  const row=this.db.query<{mfa_required:number;epoch:number},[]>('SELECT mfa_required,epoch FROM management_auth_policy WHERE singleton=1').get()!;
  return {mfa_required:row.mfa_required===1,epoch:row.epoch};
 }
 setPolicy(required:boolean):void {
  this.db.query('UPDATE management_auth_policy SET mfa_required=?,epoch=epoch+1 WHERE singleton=1').run(required?1:0);
 }
 grantPassword(actor:string,session:string,password:number,expires:number,epoch:number,policyEpoch:number):boolean {
  return this.db.transaction(()=>{
  if(this.epoch(actor)!==epoch||this.policy().epoch!==policyEpoch)return false;
  this.db.query('DELETE FROM management_password_grant WHERE expires<=?').run(Math.floor(Date.now()/1000));
  this.db.query(`INSERT INTO management_password_grant VALUES (?,?,?,?,?,?) ON CONFLICT(session) DO UPDATE SET
   actor=excluded.actor,password=excluded.password,epoch=excluded.epoch,policy_epoch=excluded.policy_epoch,expires=excluded.expires`)
   .run(session,actor,password,epoch,policyEpoch,expires);
  return true;
  }).immediate();
 }
 passwordGranted(actor:string,session:string,password:number,now=Math.floor(Date.now()/1000)):boolean {
  const policy=this.policy();
  return !policy.mfa_required&&!!this.db.query(`SELECT 1 FROM management_password_grant
   WHERE actor=? AND session=? AND password=? AND epoch=? AND policy_epoch=? AND expires>?`)
   .get(actor,session,password,this.epoch(actor),policy.epoch,now);
 }
 /** No token, password, email or client supplied address is persisted. */
 rate(bucket:string,limit:number,windowMs:number,now=Date.now()):boolean {
  return this.db.transaction(()=>{
   this.db.query('DELETE FROM management_rate WHERE expires<=?').run(now);
   const row=this.db.query<{count:number;expires:number},[string]>('SELECT count,expires FROM management_rate WHERE bucket=?').get(bucket);
   if(row&&row.count>=limit)return false;
   this.db.query(`INSERT INTO management_rate(bucket,count,expires) VALUES (?,1,?)
    ON CONFLICT(bucket) DO UPDATE SET count=count+1`).run(bucket,now+windowMs);
   return true;
  }).immediate();
 }
 epoch(actor:string):number {
  return this.db.query<{epoch:number},[string]>('SELECT epoch FROM management_mfa_epoch WHERE actor=?').get(actor)?.epoch??0;
 }
 /** Serialize native mutations so simultaneous removals cannot both remove the last factor. */
 lease(actor:string,now=Date.now()):string|null {
  return this.db.transaction(()=>{
   this.db.query('DELETE FROM management_mfa_lease WHERE expires<=?').run(now);
   if(this.db.query('SELECT 1 FROM management_mfa_lease WHERE actor=?').get(actor))return null;
   const nonce=randomUUID();this.db.query('INSERT INTO management_mfa_lease VALUES (?,?,?)').run(actor,nonce,now+60000);return nonce;
  }).immediate();
 }
 release(actor:string,nonce:string):void {
  this.db.query('DELETE FROM management_mfa_lease WHERE actor=? AND nonce=?').run(actor,nonce);
 }
 holds(actor:string,nonce:string,now=Date.now()):boolean {
  return !!this.db.query('SELECT 1 FROM management_mfa_lease WHERE actor=? AND nonce=? AND expires>?').get(actor,nonce,now);
 }
 grant(actor:string,session:string,factor:string,verified:number,expires:number,epoch:number):boolean {
  return this.db.transaction(()=>{
   const state=this.db.query<{epoch:number;revoked:number},[string]>('SELECT epoch,revoked FROM management_mfa_epoch WHERE actor=?').get(actor);
   if((state?.epoch??0)!==epoch||verified<=(state?.revoked??0))return false;
   this.db.query('DELETE FROM management_mfa_grant WHERE expires<=?').run(Math.floor(Date.now()/1000));
   this.db.query(`INSERT INTO management_mfa_grant(session,actor,factor,verified,expires,epoch) VALUES (?,?,?,?,?,?)
    ON CONFLICT(session) DO UPDATE SET actor=excluded.actor,factor=excluded.factor,
     verified=excluded.verified,expires=excluded.expires,epoch=excluded.epoch`).run(session,actor,factor,verified,expires,epoch);
   return true;
  }).immediate();
 }
 grantedFactor(actor:string,session:string,verified:number,factors:readonly string[],now=Math.floor(Date.now()/1000)):string|null {
  const row=this.db.query<{factor:string;epoch:number;expires:number},[string,string,number]>(
   'SELECT factor,epoch,expires FROM management_mfa_grant WHERE actor=? AND session=? AND verified=?').get(actor,session,verified);
  return row&&row.expires>now&&row.epoch===this.epoch(actor)&&factors.includes(row.factor)?row.factor:null;
 }
 granted(actor:string,session:string,verified:number,factors:readonly string[],now=Math.floor(Date.now()/1000)):boolean {
  return this.grantedFactor(actor,session,verified,factors,now)!==null;
 }
 /** First verified enrollment ends password and Studio sessions without rejecting the new verification. */
 promote(actor:string):void {
  this.db.query(`INSERT INTO management_mfa_epoch(actor,epoch,revoked) VALUES (?,1,0)
   ON CONFLICT(actor) DO UPDATE SET epoch=epoch+1`).run(actor);
  this.db.query('DELETE FROM management_password_grant WHERE actor=?').run(actor);
  this.db.query('DELETE FROM management_mfa_grant WHERE actor=?').run(actor);
 }
 revoke(actor:string):void {
  this.db.transaction(()=>{
   this.db.query(`INSERT INTO management_mfa_epoch(actor,epoch,revoked) VALUES (?,1,?)
    ON CONFLICT(actor) DO UPDATE SET epoch=epoch+1,revoked=MAX(revoked,excluded.revoked)`).run(actor,Math.floor(Date.now()/1000));
   this.db.query('DELETE FROM management_mfa_grant WHERE actor=?').run(actor);
   this.db.query('DELETE FROM management_password_grant WHERE actor=?').run(actor);
  }).immediate();
 }
}
