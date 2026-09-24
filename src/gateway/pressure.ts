import type {ConcurrencyGate} from './concurrency';

export type Saturation={minutes:number;refused:number;peak:number;guarantee:number};

/** Samples a gate once a minute. An environment is saturated in a minute when it was refused at
 * its share or ceiling, or when a neighbour within its share was turned away while it borrowed.
 * Borrowing idle room alone is the intended behaviour and never counts. After `minutes` saturated minutes in a
 * row it reports once, with the run's refusals and peak, and starts counting again. A minute
 * without saturation ends the run. docs/engineering/FAIR-SHARE-ADMISSION.md */
export class PressureMonitor {
 private runs=new Map<string,Saturation>();
 private timer:ReturnType<typeof setInterval>|undefined;
 constructor(private gate:ConcurrencyGate,private report:(runtime:string,saturation:Saturation)=>void,private minutes=15) {
  if(!Number.isSafeInteger(minutes)||minutes<1)throw new Error('Invalid saturation window');
 }
 sample() {
  const seen=new Set<string>();
  for(const [runtime,pressure] of this.gate.pressure()){
   if(!(pressure.refused>0||pressure.squeezed>0))continue;
   seen.add(runtime);
   const run=this.runs.get(runtime)??{minutes:0,refused:0,peak:0,guarantee:pressure.guarantee};
   run.minutes++;run.refused+=pressure.refused+pressure.squeezed;run.peak=Math.max(run.peak,pressure.peak);
   if(run.minutes>=this.minutes){
    this.runs.delete(runtime);
    try {this.report(runtime,{...run});}catch {/* a notice must never take the gateway down */}
   }else this.runs.set(runtime,run);
  }
  for(const runtime of [...this.runs.keys()])if(!seen.has(runtime))this.runs.delete(runtime);
 }
 start(intervalMs=60_000) {
  if(this.timer)return;
  this.timer=setInterval(()=>this.sample(),intervalMs);this.timer.unref?.();
 }
 stop() {clearInterval(this.timer);this.timer=undefined;}
}
