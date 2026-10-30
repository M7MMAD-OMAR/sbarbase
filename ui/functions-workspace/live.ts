import {useEffect,useRef,useState} from 'react';
/** Like useData, but a reload keeps the numbers on screen until the new ones arrive, and `every` (milliseconds, 0 for never)
 * reloads by itself. Changing a dependency reloads; the caller keys the component when what it shows must not carry over. */
export function useLive<T>(load:(signal:AbortSignal)=>Promise<T>,dependencies:unknown[],every=0){
 const [data,setData]=useState<T>(),[error,setError]=useState(''),[loading,setLoading]=useState(true),[tick,setTick]=useState(0);
 const latest=useRef(load);latest.current=load;
 useEffect(()=>{
  const controller=new AbortController();setLoading(true);
  latest.current(controller.signal).then(value=>{if(!controller.signal.aborted){setData(value);setError('');}})
   .catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:'Request failed.');})
   .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
  return()=>controller.abort();
 },[...dependencies,tick]);
 useEffect(()=>{if(!every)return;const timer=setInterval(()=>setTick(value=>value+1),every);return()=>clearInterval(timer);},[every]);
 return {data,error,loading,refresh:()=>setTick(value=>value+1)};
}

/** The value after it has stopped changing for `delay` milliseconds, so typing in a filter sends one request, not one per key. */
export function useDebounced<T>(value:T,delay=300):T {
 const [settled,setSettled]=useState(value);
 useEffect(()=>{const timer=setTimeout(()=>setSettled(value),delay);return()=>clearTimeout(timer);},[value,delay]);
 return settled;
}
