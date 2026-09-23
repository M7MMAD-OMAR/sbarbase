import {readFileSync,statSync} from 'node:fs';

const cache=new Map<string,{identity:string;value:unknown}>();

/** Parse a JSON file once per version of it. The identity changes on every rename and every
 * in-place write, so a replaced or rewritten file is read again on the next call. A missing
 * file throws exactly as readFileSync does. The value is shared: callers must not mutate it. */
export function readJsonCached(path:string):unknown {
  const info=statSync(path,{bigint:true});
  const identity=`${info.dev}:${info.ino}:${info.size}:${info.mtimeNs}:${info.ctimeNs}`;
  const cached=cache.get(path);
  if(cached?.identity===identity)return cached.value;
  const value:unknown=JSON.parse(readFileSync(path,'utf8'));
  cache.set(path,{identity,value});
  return value;
}
