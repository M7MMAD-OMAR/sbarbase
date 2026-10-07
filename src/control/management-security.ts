import type {Database} from 'bun:sqlite';
import {randomUUID} from 'node:crypto';

/** Durable admission and native MFA grants, shared by every controller using the catalog. */
export class ManagementSecurity {
 constructor(private db:Database){
  db.exec(`CREATE TABLE IF NOT EXISTS management_rate(
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
 revoke(actor:string):void {
  this.db.transaction(()=>{
   this.db.query(`INSERT INTO management_mfa_epoch(actor,epoch,revoked) VALUES (?,1,?)
    ON CONFLICT(actor) DO UPDATE SET epoch=epoch+1,revoked=MAX(revoked,excluded.revoked)`).run(actor,Math.floor(Date.now()/1000));
   this.db.query('DELETE FROM management_mfa_grant WHERE actor=?').run(actor);
  }).immediate();
 }
}
