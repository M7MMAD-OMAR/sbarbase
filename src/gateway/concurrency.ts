/** In-process admission only. No queue and no per-key bypass of tenant limits. */
export class ConcurrencyGate {
 private total=0;
 private active=new Map<string,number>();
 constructor(private perEnvironment=8,private maximum=32,private responseTimeoutMs=30_000) {
  if(![perEnvironment,maximum,responseTimeoutMs].every(value=>Number.isSafeInteger(value)&&value>0))
   throw new Error('Invalid concurrency limits');
 }
 async run(environment:string,request:Request,forward:()=>Promise<Response>):Promise<Response> {
  if(request.signal.aborted)return Response.json({message:'Request cancelled'},{status:408});
  const count=this.active.get(environment)??0;
  const status=count>=this.perEnvironment?429:this.total>=this.maximum?503:0;
  if(status)return Response.json({message:'Request capacity unavailable. Retry later.'},{status,
   headers:{'retry-after':'1','cache-control':'no-store'}});
  this.active.set(environment,count+1);this.total++;
  let released=false;
  const release=()=>{
   if(released)return;released=true;this.total--;
   const remaining=(this.active.get(environment)??1)-1;
   if(remaining)this.active.set(environment,remaining);else this.active.delete(environment);
  };
  try {
   const response=await forward();
   if(!response.body){release();return response;}
   const reader=response.body.getReader();
   let timer:ReturnType<typeof setTimeout>|undefined;
   let controller:ReadableStreamDefaultController<Uint8Array>|undefined;
   let finished=false;
   const finish=()=>{if(finished)return;finished=true;clearTimeout(timer);request.signal.removeEventListener('abort',abort);release();};
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
   timer=setTimeout(abort,this.responseTimeoutMs);timer.unref?.();
   if(request.signal.aborted)abort();
   return new Response(body,{status:response.status,statusText:response.statusText,headers:response.headers});
  }catch(error){release();throw error;}
 }
}
