/** In-process admission only. No queue and no per-key bypass of tenant limits. */
export class ConcurrencyGate {
 private total=0;
 private services=new Map<string,number>();
 private active=new Map<string,number>();
 constructor(private perEnvironment=8,private maximum=32,private responseTimeoutMs=30_000,private forwardTimeoutMs=30_000) {
  if(![perEnvironment,maximum,responseTimeoutMs,forwardTimeoutMs].every(value=>Number.isSafeInteger(value)&&value>0))
   throw new Error('Invalid concurrency limits');
 }
 async run(environment:string,request:Request,forward:(signal:AbortSignal)=>Promise<Response>,budget?:{service:string;maximum:number}):Promise<Response> {
  if(request.signal.aborted)return Response.json({message:'Request cancelled'},{status:408});
  if(budget&&(!Number.isSafeInteger(budget.maximum)||budget.maximum<1))
   return Response.json({message:'Invalid service capacity'},{status:503});
  const serviceKey=budget?JSON.stringify([environment,budget.service]):undefined;
  const serviceCount=serviceKey?this.services.get(serviceKey)??0:0;
  const count=this.active.get(environment)??0;
  const status=count>=this.perEnvironment||(budget&&serviceCount>=budget.maximum)?429:this.total>=this.maximum?503:0;
  if(status)return Response.json({message:'Request capacity unavailable. Retry later.'},{status,
   headers:{'retry-after':'1','cache-control':'no-store'}});
  this.active.set(environment,count+1);this.total++;
  if(serviceKey)this.services.set(serviceKey,serviceCount+1);
  let released=false;
  const release=()=>{
   if(released)return;released=true;this.total--;
   if(serviceKey){
    const remaining=(this.services.get(serviceKey)??1)-1;
    if(remaining)this.services.set(serviceKey,remaining);else this.services.delete(serviceKey);
   }
   const remaining=(this.active.get(environment)??1)-1;
   if(remaining)this.active.set(environment,remaining);else this.active.delete(environment);
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
   let finished=false;
   const finish=()=>{if(finished)return;finished=true;clearTimeout(responseTimer);request.signal.removeEventListener('abort',abort);release();};
   const abort=()=>{
    if(finished)return;
    finish();void reader.cancel().catch(()=>{});
    // A disconnected client cannot receive an error. Close its stream cleanly;
    // a deadline on a still-connected client must fail, never truncate silently.
    if(request.signal.aborted)controller?.close();
    else controller?.error(new Error('Response stream deadline exceeded'));
   };
   const body=new ReadableStream<Uint8Array>({
    start(value){controller=value;},
    async pull(value){
     try {
      const item=await reader.read();
      if(finished)return;
      if(item.done){finish();value.close();}else value.enqueue(item.value);
     }catch(error){if(!finished){finish();value.error(error);}}
    },
    cancel(reason){finish();void reader.cancel(reason).catch(()=>{});},
   });
   request.signal.addEventListener('abort',abort,{once:true});
   responseTimer=setTimeout(abort,this.responseTimeoutMs);responseTimer.unref?.();
   if(request.signal.aborted)abort();
   return new Response(body,{status:response.status,statusText:response.statusText,headers:response.headers});
  }catch(error){release();throw error;}
 }
}
