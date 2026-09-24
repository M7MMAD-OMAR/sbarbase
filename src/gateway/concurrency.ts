/** Borrowing above an environment's guaranteed share: up to `ceiling` in flight, and only while
 * `headroom` slots of the whole gateway stay free for environments still within their share.
 * See docs/engineering/FAIR-SHARE-ADMISSION.md. */
export type BorrowPolicy={ceiling:number;headroom:number};
/** What one environment did since the last sample: 429s it received and its peak in flight. */
export type EnvironmentPressure={refused:number;peak:number;active:number;guarantee:number};

/** In-process admission only. No queue and no per-key bypass of tenant limits. */
export class ConcurrencyGate {
 private total=0;
 private paused=new Map<string,symbol>();
 private drainWaiters=new Map<string,Set<()=>void>>();
 /** Pause applies to this process only. A drained gateway does not prove SQL stopped. */
 pause(environment:string) {
  if(!environment||this.paused.has(environment))throw new Error('Environment already paused or invalid');
  const lease=Symbol(environment);this.paused.set(environment,lease);
  return {
   waitForDrain:(timeoutMs:number):Promise<boolean>=>{
    if(!Number.isSafeInteger(timeoutMs)||timeoutMs<1)throw new Error('Invalid drain deadline');
    if(this.paused.get(environment)!==lease)throw new Error('Pause lease expired');
    if(!(this.active.get(environment)??0))return Promise.resolve(true);
    return new Promise(resolve=>{
     const waiters=this.drainWaiters.get(environment)??new Set<()=>void>();
     this.drainWaiters.set(environment,waiters);
     const finish=(drained:boolean)=>{clearTimeout(timer);waiters.delete(done);if(!waiters.size)this.drainWaiters.delete(environment);resolve(drained);};
     const done=()=>finish(!(this.active.get(environment)??0)&&this.paused.get(environment)===lease);
     const timer=setTimeout(()=>finish(false),timeoutMs);
     waiters.add(done);
    });
   },
   resume:()=>{
    if(this.paused.get(environment)!==lease)throw new Error('Pause lease expired');
    this.paused.delete(environment);
    this.notifyDrain(environment);
   },
  };
 }

 private notifyDrain(environment:string) {
  for(const done of [...(this.drainWaiters.get(environment)??[])])done();
 }
 private services=new Map<string,number>();
 private active=new Map<string,number>();
 private refused=new Map<string,number>();
 private peaks=new Map<string,number>();
 /** Without a borrow policy the guarantee is also the ceiling, as before. */
 constructor(private perEnvironment=8,private maximum=32,private responseTimeoutMs=30_000,private forwardTimeoutMs=30_000,private borrow?:BorrowPolicy) {
  if(![perEnvironment,maximum,responseTimeoutMs,forwardTimeoutMs].every(value=>Number.isSafeInteger(value)&&value>0))
   throw new Error('Invalid concurrency limits');
  if(borrow&&!(Number.isSafeInteger(borrow.ceiling)&&Number.isSafeInteger(borrow.headroom)&&borrow.ceiling>=perEnvironment
   &&borrow.ceiling<=maximum&&borrow.headroom>=0&&borrow.headroom<maximum))
   throw new Error('Invalid borrow policy');
 }
 /** Per environment pressure since the previous call, then starts a new sample. */
 pressure():Map<string,EnvironmentPressure> {
  const out=new Map<string,EnvironmentPressure>();
  for(const environment of new Set([...this.refused.keys(),...this.peaks.keys(),...this.active.keys()])){
   const active=this.active.get(environment)??0;
   out.set(environment,{refused:this.refused.get(environment)??0,peak:Math.max(this.peaks.get(environment)??0,active),active,guarantee:this.perEnvironment});
  }
  this.refused.clear();this.peaks.clear();
  return out;
 }
 private admission(count:number,serviceFull:boolean):0|429|503 {
  if(serviceFull)return 429;
  if(count<this.perEnvironment)return this.total>=this.maximum?503:0;
  if(!this.borrow||count>=this.borrow.ceiling)return 429;
  return this.total>=this.maximum-this.borrow.headroom?429:0;
 }
 async run(environment:string,request:Pick<Request,'signal'>,forward:(signal:AbortSignal)=>Promise<Response>,budget?:{service:string;maximum:number;drainOnCancel?:boolean}):Promise<Response> {
  if(this.paused.has(environment))return Response.json({message:'Environment temporarily paused'},{status:503,headers:{'retry-after':'1','cache-control':'no-store'}});
  if(request.signal.aborted)return Response.json({message:'Request cancelled'},{status:408});
  if(budget&&(!Number.isSafeInteger(budget.maximum)||budget.maximum<1))
   return Response.json({message:'Invalid service capacity'},{status:503});
  const serviceKey=budget?JSON.stringify([environment,budget.service]):undefined;
  const serviceCount=serviceKey?this.services.get(serviceKey)??0:0;
  const count=this.active.get(environment)??0;
  const status=this.admission(count,!!budget&&serviceCount>=budget.maximum);
  if(status===429)this.refused.set(environment,(this.refused.get(environment)??0)+1);
  if(status)return Response.json({message:'Request capacity unavailable. Retry later.'},{status,
   headers:{'retry-after':'1','cache-control':'no-store'}});
  this.active.set(environment,count+1);this.total++;
  if(count+1>(this.peaks.get(environment)??0))this.peaks.set(environment,count+1);
  if(serviceKey)this.services.set(serviceKey,serviceCount+1);
  let released=false;
  const release=()=>{
   if(released)return;released=true;this.total--;
   if(serviceKey){
    const remaining=(this.services.get(serviceKey)??1)-1;
    if(remaining)this.services.set(serviceKey,remaining);else this.services.delete(serviceKey);
   }
   const remaining=(this.active.get(environment)??1)-1;
   if(remaining)this.active.set(environment,remaining);else {
    this.active.delete(environment);
    this.notifyDrain(environment);
   }
  };
  try {
   const upstream=new AbortController();
   let abandoned=false;
   let timer:ReturnType<typeof setTimeout>|undefined;
   let stop!:(status:408|504)=>void;
   const interrupted=new Promise<408|504>(resolve=>{
    stop=status=>{abandoned=true;resolve(status);upstream.abort();};
   });
   const cancelled=()=>stop(408);
   request.signal.addEventListener('abort',cancelled,{once:true});
   timer=setTimeout(()=>stop(504),this.forwardTimeoutMs);timer.unref?.();
   let result:Response|408|504;
   try {
    if(request.signal.aborted)cancelled();
    const pending=abandoned?Promise.resolve(408 as const):forward(upstream.signal).then(response=>{
     if(abandoned)void response.body?.cancel().catch(()=>{});
     return response;
    });
    result=await Promise.race([pending,interrupted]);
   }finally {
    clearTimeout(timer);request.signal.removeEventListener('abort',cancelled);
   }
   if(typeof result==='number'){
    release();return Response.json({message:result===504?'Upstream deadline exceeded':'Request cancelled'},
     {status:result,headers:{'cache-control':'no-store'}});
   }
   const response=result;
   if(!response.body){release();return response;}
   const reader=response.body.getReader();
   let responseTimer:ReturnType<typeof setTimeout>|undefined;
   let controller:ReadableStreamDefaultController<Uint8Array>|undefined;
   let finished=false,draining=false;
   const finish=()=>{if(finished)return;finished=true;clearTimeout(responseTimer);request.signal.removeEventListener('abort',abort);release();};
   const abort=()=>{
    if(finished)return;
    finish();void reader.cancel().catch(()=>{});
    // A disconnected client cannot receive an error. Close its stream cleanly;
    // a deadline on a still-connected client must fail, never truncate silently.
    if(draining)return;
    if(request.signal.aborted)controller?.close();
    else controller?.error(new Error('Response stream deadline exceeded'));
   };
   const body=new ReadableStream<Uint8Array>({
    start(value){controller=value;},
    async pull(value){
     try {
      const item=await reader.read();
      if(finished||draining)return;
      if(item.done){finish();value.close();}else value.enqueue(item.value);
     }catch(error){if(!finished){finish();if(!draining)value.error(error);}}
    },
    cancel(reason){
     if(!budget?.drainOnCancel){finish();void reader.cancel(reason).catch(()=>{});return;}
     // Keep admission while an abandoned REST response drains, without buffering.
     draining=true;
     void (async()=>{
      try {while(!finished){const part=await reader.read();if(part.done)break;}}
      catch {} finally {finish();}
     })();
    },
   });
   request.signal.addEventListener('abort',abort,{once:true});
   responseTimer=setTimeout(abort,this.responseTimeoutMs);responseTimer.unref?.();
   if(request.signal.aborted)abort();
   return new Response(body,{status:response.status,statusText:response.statusText,headers:response.headers});
  }catch(error){release();throw error;}
 }
}
