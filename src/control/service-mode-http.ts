import {isMode,validIdleMinutes,type Mode} from './on-demand-policy';

/** The body of PUT `/environments/{id}/realtime` and `/functions`: either the original `{"enabled": true|false}` (On or Off, as before) or
 * `{"mode": "off"|"on"|"on_demand", "idle_minutes"?: n}` (the quiet period of On demand in minutes, 5 or more). Anything else is refused. */
export type ServiceChange={kind:'enabled';enabled:boolean}|{kind:'mode';mode:Mode;idle?:number};
export const SERVICE_CHANGE_HELP='Send {"enabled": true}, {"enabled": false} or {"mode": "off" | "on" | "on_demand", "idle_minutes": 30}';

export function parseServiceChange(input:unknown):ServiceChange|null {
 if(!input||typeof input!=='object'||Array.isArray(input))return null;
 const body=input as Record<string,unknown>,keys=Object.keys(body);
 if(keys.length===1&&typeof body.enabled==='boolean')return {kind:'enabled',enabled:body.enabled};
 if(keys.some(key=>key!=='mode'&&key!=='idle_minutes')||!isMode(body.mode))return null;
 if(body.idle_minutes!==undefined&&!validIdleMinutes(body.idle_minutes))return null;
 return {kind:'mode',mode:body.mode,idle:body.idle_minutes as number|undefined};
}
