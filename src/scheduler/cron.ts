/** pg_cron 1.6.4's schedule syntax and when a schedule is due (docs/engineering/SCHEDULED-JOBS.md).
 *
 * Read from `parse_cron_entry`, `get_range` and `get_number` in pg_cron's src/entry.c, `TryParseInterval`
 * in src/job_metadata.c and `ShouldRunTask` in src/pg_cron.c. Two deliberate differences: text after the
 * fifth field, or an unparsable character inside a field, is refused here where pg_cron ignores it, and
 * `@yearly` runs on 1 January where pg_cron 1.6.4 never runs it. `@reboot` is refused.
 *
 * The plpgsql validator in lab/scheduled_jobs.py (`cron.schedule_valid`) accepts the same schedules;
 * lab/scheduled-jobs-check.ts compares the two on a corpus. */

export type CronSchedule={kind:'cron';minute:boolean[];hour:boolean[];dom:boolean[];month:boolean[];dow:boolean[];
 domStar:boolean;dowStar:boolean;domLast:boolean;wild:boolean};
export type IntervalSchedule={kind:'interval';seconds:number};
export type Schedule=CronSchedule|IntervalSchedule;

const MONTHS=['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'];
const DAYS=['sun','mon','tue','wed','thu','fri','sat','sun'];
export const MAX_SCHEDULE=1000;

function bits(size:number,on=false){return Array.from({length:size},()=>on);}

/** One field: `*`, a number or a name, `a-b`, each optionally followed by `/step` (but a step after a
 * single number is refused, as in pg_cron), joined by commas. */
function field(text:string,low:number,high:number,names?:string[]):boolean[]|null {
 const set=bits(high-low+1);
 const number=(value:string):number|null=>{
  if(!/^[A-Za-z0-9]+$/.test(value))return null;
  if(names){const index=names.indexOf(value.toLowerCase());if(index>=0)return index+low;}
  return /^[0-9]{1,9}$/.test(value)?Number(value):null;
 };
 for(const part of text.split(',')){
  const match=part.match(/^(\*|[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?)(?:\/([0-9]{1,9}))?$/);
  if(!match)return null;
  const [,range,stepText]=match;
  let first:number,last:number;
  if(range==='*'){first=low;last=high;}
  else {
   const [a,b]=range!.split('-');
   const start=number(a!);if(start===null)return null;
   if(b===undefined){
    if(stepText!==undefined)return null;
    if(start<low||start>high)return null;
    set[start-low]=true;continue;
   }
   const stop=number(b);if(stop===null)return null;
   first=start;last=stop;
  }
  const step=stepText===undefined?1:Number(stepText);
  if(!Number.isSafeInteger(step)||step<=0)return null;
  if(first<low||first>high||last<low||last>high)return null;
  for(let i=first;i<=last;i+=step)set[i-low]=true;
 }
 return set;
}

function macro(name:string):CronSchedule|null {
 const all=(size:number)=>bits(size,true),one=(size:number,index:number)=>{const set=bits(size);set[index]=true;return set;};
 const base={domLast:false,domStar:false,dowStar:false,wild:false};
 switch(name){
  case 'yearly':case 'annually':return {kind:'cron',...base,minute:one(60,0),hour:one(24,0),dom:one(31,0),month:one(12,0),dow:all(8),dowStar:true};
  case 'monthly':return {kind:'cron',...base,minute:one(60,0),hour:one(24,0),dom:one(31,0),month:all(12),dow:all(8),dowStar:true};
  case 'weekly':return {kind:'cron',...base,minute:one(60,0),hour:one(24,0),dom:all(31),month:all(12),dow:sunday(one(8,0)),dowStar:true};
  case 'daily':case 'midnight':return {kind:'cron',...base,minute:one(60,0),hour:one(24,0),dom:all(31),month:all(12),dow:all(8)};
  case 'hourly':return {kind:'cron',...base,minute:one(60,0),hour:all(24),dom:all(31),month:all(12),dow:all(8),wild:true};
  default:return null;
 }
}

function sunday(dow:boolean[]){if(dow[0]||dow[7]){dow[0]=true;dow[7]=true;}return dow;}

/** The schedule, or null when pg_cron would refuse it (or this parser does, see above). */
export function parseSchedule(text:unknown):Schedule|null {
 if(typeof text!=='string'||text.length===0||text.length>=MAX_SCHEDULE)return null;
 const interval=text.toLowerCase().match(/^[ \t\n\r\f\v]*([0-9]+)[ \t\n\r\f\v]*seconds?[ \t\n\r\f\v]*$/);
 if(interval){const digits=interval[1]!.replace(/^0+/,'');const seconds=digits.length<=2?Number(digits):0;return seconds>0&&seconds<60?{kind:'interval',seconds}:null;}
 const trimmed=text.replace(/^[ \t\n\r\f\v]+|[ \t\n\r\f\v]+$/g,'');
 if(trimmed.startsWith('@'))return /^@[a-z]+$/.test(trimmed)?macro(trimmed.slice(1)):null;
 const parts=trimmed.split(/[ \t\n\r\f\v]+/);
 if(parts.length!==5)return null;
 const [m,h,dm,mo,dw]=parts as [string,string,string,string,string];
 const minute=field(m,0,59),hour=field(h,0,23),month=field(mo,1,12,MONTHS),dow=field(dw,0,7,DAYS);
 const domLast=dm==='$';
 const dom=domLast?bits(31):field(dm,1,31);
 if(!minute||!hour||!dom||!month||!dow)return null;
 return {kind:'cron',minute,hour,dom,month,dow:sunday(dow),domStar:dm.startsWith('*'),dowStar:dw.startsWith('*'),domLast,
  wild:m.startsWith('*')||h.startsWith('*')};
}

/** pg_cron's ShouldRunTask for the minute that starts at `at` (GMT). `wild` and `fixed` select jobs whose
 * minute or hour field starts with `*`, and the others, as its doWild and doNonWild do. */
export function due(schedule:CronSchedule,at:Date,wild=true,fixed=true):boolean {
 const tomorrow=new Date(at.getTime()+86_400_000);
 const lastDom=schedule.domLast&&tomorrow.getUTCDate()===1;
 const thisDom=lastDom||schedule.dom[at.getUTCDate()-1]===true;
 const thisDow=schedule.dow[at.getUTCDay()]===true;
 const day=schedule.domStar||schedule.dowStar?thisDom&&thisDow:thisDom||thisDow;
 if(!(schedule.minute[at.getUTCMinutes()]&&schedule.hour[at.getUTCHours()]&&schedule.month[at.getUTCMonth()]&&day))return false;
 return (fixed&&!schedule.wild)||(wild&&schedule.wild);
}

export const MINUTE=60_000;
export function minuteStart(ms:number){return ms-ms%MINUTE;}

/** The minutes a runner should look at when it notices the clock moved from `last` (a minute start) to
 * `now`, following pg_cron's StartPendingRuns: one to five minutes are each looked at; more than five and
 * up to three hours, the fixed-time jobs for each minute and the wildcard ones for the current minute; a
 * clock that went back looks at nothing and keeps `last`; a jump forward of more than three hours looks at
 * the current minute only. A job due in several of the minutes looked at runs once (one waiting run). Returns the minutes with which kinds to run, and the new last. */
export function catchUp(last:number,now:number):{minutes:{at:number;wild:boolean;fixed:boolean}[];last:number} {
 const current=minuteStart(now);
 const passed=Math.trunc((now-last)/MINUTE);
 if(passed===0)return {minutes:[],last};
 const out:{at:number;wild:boolean;fixed:boolean}[]=[];
 if(passed>0&&passed<=5){for(let at=last+MINUTE;at<=current;at+=MINUTE)out.push({at,wild:true,fixed:true});return {minutes:out,last:current};}
 if(passed>5&&passed<=180){for(let at=last+MINUTE;at<=current;at+=MINUTE)out.push({at,wild:false,fixed:true});out.push({at:current,wild:true,fixed:false});return {minutes:out,last:current};}
 // A clock that went back: wait until it passes the last minute again. pg_cron's MinutesPassed reads a negative
 // difference as 0 (TimestampDifference), so its CLOCK_JUMP_BACKWARD branch is never taken either.
 if(passed<0)return {minutes:[],last};
 return {minutes:[{at:current,wild:true,fixed:true}],last:current};
}
