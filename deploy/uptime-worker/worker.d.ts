/** Types for worker.js, for the tests that import it. */
export type UptimeState={down:boolean;failures:number;at:number;last:string|null;since?:number};
export function run(env:Record<string,any>,now?:number):Promise<UptimeState>;
declare const worker:{scheduled:(...args:any[])=>Promise<void>;fetch:(...args:any[])=>Promise<Response>};
export default worker;
