/** When each optional service of an environment was last used, and what is using it right now.
 *
 * The gateway tells this object about a Realtime socket opening or closing and about an Edge Functions call starting, after
 * the call's API key was verified: a request that failed authentication never counts as use. The times are kept in
 * memory and written to the catalog at most once a minute (`flush`), so the Services page can say "last used" across
 * a restart and the sleeping logic (src/control/on-demand.ts) never decides from a process that just began.
 * Nothing here holds a key, a token, a header or a path. */
export type ActivityStore={
 load(runtime:string):Record<string,number>;
 save(rows:{runtime:string;service:string;at:number}[]):void;
};
export type TrackedService='realtime'|'functions';
/** Edge Functions calls in flight for a runtime: the gateway's own count (ConcurrencyGate.inFlight), so a call counts until its answer has ended. */
export type InFlight=(runtime:string)=>number;

const FLUSH_MS=60_000;

export class ServiceActivity {
 private last=new Map<string,number>();
 private sockets=new Map<string,number>();
 private dirty=new Set<string>();
 private timer?:ReturnType<typeof setInterval>;
 /** When this process began: a service is never judged idle from a time before it. */
 readonly startedAt:number;
 constructor(private store?:ActivityStore,private now:()=>number=Date.now,private functionsInFlight:InFlight=()=>0) {this.startedAt=now();}

 private key(runtime:string,service:TrackedService){return runtime+'|'+service;}
 private mark(runtime:string,service:TrackedService) {
  const key=this.key(runtime,service);
  this.last.set(key,this.now());this.dirty.add(key);
 }
 /** A request reached a service of this environment. */
 touch(runtime:string,service:TrackedService) {this.mark(runtime,service);}
 /** A Realtime socket was accepted; the returned function is called once when it closes. */
 socketOpened(runtime:string):()=>void {
  this.sockets.set(runtime,(this.sockets.get(runtime)??0)+1);this.mark(runtime,'realtime');
  let closed=false;
  return ()=>{
   if(closed)return;closed=true;
   const remaining=(this.sockets.get(runtime)??1)-1;
   if(remaining>0)this.sockets.set(runtime,remaining);else this.sockets.delete(runtime);
   this.mark(runtime,'realtime');
  };
 }
 openSockets(runtime:string){return this.sockets.get(runtime)??0;}
 inFlightCalls(runtime:string){return this.functionsInFlight(runtime);}
 /** Last use in milliseconds, or null: what this process saw, or what an earlier one recorded. */
 lastUsed(runtime:string,service:TrackedService):number|null {
  const seen=this.last.get(this.key(runtime,service));
  let stored:number|undefined;
  try{stored=this.store?.load(runtime)[service];}catch{}
  const best=Math.max(seen??0,stored??0);
  return best>0?best:null;
 }
 /** Whether the service has been quiet for `idleMs`: nothing open, nothing in flight, no use in that time, and this
  * process has itself been up that long. */
 quietFor(runtime:string,service:TrackedService,idleMs:number):boolean {
  const open=service==='realtime'?this.openSockets(runtime):this.inFlightCalls(runtime);
  if(open>0)return false;
  const now=this.now();
  if(now-this.startedAt<idleMs)return false;
  const last=this.lastUsed(runtime,service);
  return last===null||now-last>=idleMs;
 }
 flush() {
  if(!this.store||!this.dirty.size)return;
  const rows=[...this.dirty].map(key=>{const [runtime,service]=key.split('|') as [string,string];return {runtime,service,at:this.last.get(key)!};});
  try{this.store.save(rows);this.dirty.clear();}catch{}
 }
 start() {
  if(this.timer)return;
  this.timer=setInterval(()=>this.flush(),FLUSH_MS);this.timer.unref?.();
 }
 stop() {if(this.timer)clearInterval(this.timer);this.timer=undefined;this.flush();}
}
