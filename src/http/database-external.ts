import type {ConnectionGuard} from './database-proxy';

/** What the external database listener admits (docs/engineering/EXTERNAL-DATABASE.md).
 *
 * An environment is reachable here only while its external access is on (`enabled`), every
 * connection attempt spends from a per-address and a total budget, and a run of failed passwords
 * for one environment closes it for a minute. A success does not clear the count (a busy pool would
 * keep wiping it); the window ages it out. An admitted session is ended within a second of its
 * environment losing external access. PostgreSQL still checks the password itself (SCRAM). */

export type GuardLimits={perAddress:number;total:number;windowMs:number;failures:number;lockMs:number};
export const DEFAULT_LIMITS:GuardLimits={perAddress:30,total:120,windowMs:10_000,failures:10,lockMs:60_000};

export class ExternalGuard implements ConnectionGuard {
 private readonly address=new Map<string,number[]>();
 private total:number[]=[];
 private readonly failed=new Map<string,number[]>();
 private readonly locked=new Map<string,number>();
 private readonly sessions=new Map<string,Set<()=>void>>();
 private sweeper:ReturnType<typeof setInterval>|undefined;
 constructor(private readonly enabled:(runtime:string)=>boolean,private readonly log:(line:string)=>void=()=>{},
  private readonly limits:GuardLimits=DEFAULT_LIMITS,private readonly now:()=>number=Date.now){}
 private recent(list:number[]|undefined,at:number,span=this.limits.windowMs){return (list??[]).filter(time=>at-time<span);}
 accept(remote:string){
  const at=this.now();
  const mine=this.recent(this.address.get(remote),at);
  this.total=this.recent(this.total,at);
  if(mine.length>=this.limits.perAddress||this.total.length>=this.limits.total)return 'Too many connection attempts; try again shortly';
  mine.push(at);this.total.push(at);
  this.address.set(remote,mine);
  if(this.address.size>1024)for(const [key,list] of this.address)if(!this.recent(list,at).length)this.address.delete(key);
  return undefined;
 }
 admit(runtime:string,remote:string){
  const at=this.now();
  const until=this.locked.get(runtime);
  if(until&&until>at)return 'Too many failed logins; try again in a minute';
  if(until)this.locked.delete(runtime);
  if(!this.enabled(runtime))return 'External database access is not enabled for this environment';
  void remote;
  return undefined;
 }
 session(runtime:string,close:()=>void){
  let set=this.sessions.get(runtime);
  if(!set)this.sessions.set(runtime,set=new Set());
  set.add(close);
  this.sweeper??=setInterval(()=>this.sweep(),1000).unref();
  return ()=>{
   const open=this.sessions.get(runtime);
   open?.delete(close);
   if(open&&!open.size)this.sessions.delete(runtime);
   if(!this.sessions.size&&this.sweeper){clearInterval(this.sweeper);this.sweeper=undefined;}
  };
 }
 /** Ends the sessions of every environment that no longer has external access. */
 sweep(){
  for(const [runtime,set] of [...this.sessions]){
   let on=false;
   try{on=this.enabled(runtime);}catch{}
   if(on)continue;
   this.log(`external database sessions of ${runtime} ended: external access is off`);
   for(const close of [...set])close();
  }
 }
 outcome(runtime:string,_remote:string,result:'ok'|'bad-password'){
  if(result==='ok')return;
  const at=this.now();
  const list=this.recent(this.failed.get(runtime),at,this.limits.lockMs);
  list.push(at);this.failed.set(runtime,list);
  if(list.length>=this.limits.failures){
   this.locked.set(runtime,at+this.limits.lockMs);this.failed.delete(runtime);
   this.log(`external database access for ${runtime} closed for ${this.limits.lockMs/1000} s after ${list.length} failed logins`);
  }
 }
}

/** SBARBASE_DATABASE_EXTERNAL_PORT (6545 by default; `off` turns it off) and
 * SBARBASE_DATABASE_EXTERNAL_BIND (127.0.0.1 by default: a tunnel connector on the server reaches
 * it; any other address publishes it, which docs/guides/database-access.md explains). */
export function externalListen(env:Record<string,string|undefined>=process.env):{port:number;host:string}|null {
 const value=(env.SBARBASE_DATABASE_EXTERNAL_PORT??'').trim();
 if(value==='off')return null;
 const port=value===''?6545:Number(value);
 if(!Number.isInteger(port)||port<1024||port>65535)throw new Error('SBARBASE_DATABASE_EXTERNAL_PORT must be a port from 1024 to 65535, or off');
 const host=(env.SBARBASE_DATABASE_EXTERNAL_BIND??'').trim()||'127.0.0.1';
 if(!/^(\d{1,3}(\.\d{1,3}){3}|::|::1|localhost)$/.test(host))throw new Error('SBARBASE_DATABASE_EXTERNAL_BIND must be an IP address');
 return {port,host};
}
