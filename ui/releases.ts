/** Pure logic behind the console's update notice, panel and progress view.
 *
 * Nothing here touches the network, the DOM or storage at module scope, so the Bun tests
 * import it directly. The shapes mirror GET /management/v1/updates.
 */
export type UpdateClass='safe'|'rebuild'|'manual';
export type UpdateSettings={check:boolean;automatic:boolean;window:{start:string;end:string}};
export type UpdatePhase='applied'|'confirmed'|'rolling_back'|'rolled_back'|'rollback_failed'|'failed';
export type UpdateRecord={phase:UpdatePhase;from:string;to:string;version?:string;startedAt:string;finishedAt?:string;automatic:boolean;failure?:string};
export type UpdateRequest={kind:'apply'|'rollback'|'check';version?:string;state:'requested'|'running'|'done'|'failed';requestedAt:string;detail?:string};
export type AvailableRelease={version:string;tag:string;commit:string;class:UpdateClass;signed:boolean;reasons:string[];notes:{en:string;ar:string};changes:{label:string;before:string;after:string}[]};
export type UpdatesView={
 current:{version:string;commit:string};
 available:null|AvailableRelease;
 refusals:string[];
 checkedAt:string|null;checkError:string|null;
 settings:UpdateSettings;
 last:null|UpdateRecord;
 request:null|UpdateRequest;
 canRollback:boolean;
};

export const UPGRADES_GUIDE='https://github.com/M7MMAD-OMAR/sbarbase/blob/main/docs/guides/upgrades.md';
export const BACKUP_GUIDE='https://github.com/M7MMAD-OMAR/sbarbase/blob/main/docs/guides/backup-and-restore.md';

/** How each release class is named and explained. The label is always shown as text, so
 * the class never depends on colour alone. */
export const CLASS_WORDS:Record<UpdateClass,{label:string;status:string;explanation:string}>={
 safe:{label:'Safe',status:'ready to install',
  explanation:'This release changes Sbarbase itself and the pinned service images only. It can be installed from here, and it returns to this version by itself if it does not start healthy.'},
 rebuild:{label:'Needs a rebuild',status:'needs a manual rebuild',
  explanation:'This release changes the container image or the systemd unit. A restart alone would not pick that up, so it is installed on the server, not from here.'},
 manual:{label:'Needs a migration',status:'needs a manual migration',
  explanation:'This release changes the database image or carries a data migration. It is never installed from here or automatically; follow the upgrades guide.'}};

/** The one line the banner states for an available release. */
export function availableText(release:Pick<AvailableRelease,'version'|'class'>){
 return `Sbarbase ${release.version} ${CLASS_WORDS[release.class].status}.`;
}

/** Release notes in the console's language, falling back to English. */
export function releaseNotes(notes:{en:string;ar:string},language:string){
 return language.toLowerCase().startsWith('ar')&&notes.ar.trim()?{text:notes.ar,language:'ar'}:{text:notes.en,language:'en'};
}

export type Banner=
 |{kind:'available';key:string;version:string;class:UpdateClass;dismissible:true}
 |{kind:'confirmed';key:string;version:string;dismissible:true}
 |{kind:'rolled_back';key:string;dismissible:true}
 |{kind:'rollback_failed';key:string;dismissible:false};

function recordVersion(last:UpdateRecord){return last.version??last.to.slice(0,12);}

/** Which banners an operator sees, most urgent first. An available release is dismissed per
 * version; a finished upgrade per record. A failed way back cannot be dismissed. */
export function banners(view:UpdatesView|undefined,dismissed:readonly string[]):Banner[]{
 if(!view)return [];
 const shown:Banner[]=[];const last=view.last;
 if(last){
  const key=`last:${last.startedAt}:${last.phase}`;
  if(last.phase==='rollback_failed')shown.push({kind:'rollback_failed',key,dismissible:false});
  else if(!dismissed.includes(key)){
   if(last.phase==='confirmed')shown.push({kind:'confirmed',key,version:recordVersion(last),dismissible:true});
   // Only the automatic way back gets a banner: an operator who rolled back asked for it.
   else if(last.phase==='rolled_back'&&last.automatic)shown.push({kind:'rolled_back',key,dismissible:true});
  }
 }
 const release=view.available;
 if(release){
  const key=`available:${release.version}`;
  if(!dismissed.includes(key))shown.push({kind:'available',key,version:release.version,class:release.class,dismissible:true});
 }
 return shown;
}

const DISMISSED='sbarbase.updates.dismissed';
type KeyStore=Pick<Storage,'getItem'|'setItem'>;
/** Dismissed banner keys. Storage can be missing or throw (private windows, blocked site
 * data); the banner then simply shows again. */
export function readDismissed(storage:KeyStore|undefined):string[]{
 try{const value:unknown=JSON.parse(storage?.getItem(DISMISSED)??'[]');return Array.isArray(value)?value.filter((item):item is string=>typeof item==='string'):[];}
 catch{return [];}
}
export function dismiss(storage:KeyStore|undefined,key:string):string[]{
 const next=[...readDismissed(storage).filter(item=>item!==key),key].slice(-20);
 try{storage?.setItem(DISMISSED,JSON.stringify(next));}catch{}
 return next;
}

/** Whether "Install update" is offered, and why not when it is not. */
export function installState(view:UpdatesView|undefined):{enabled:boolean;reasons:string[]}{
 const release=view?.available;
 if(!view||!release)return {enabled:false,reasons:['No newer release is available.']};
 const reasons:string[]=[];
 if(release.class!=='safe')reasons.push('Only a safe release installs from the console.');
 if(!release.signed)reasons.push('This release is not signed by a Sbarbase release key.');
 if(view.refusals.length)reasons.push('The server cannot apply it right now.');
 if(busy(view))reasons.push('Another update request is still in progress.');
 return {enabled:reasons.length===0,reasons};
}

/** A request the server has not finished, or an upgrade that is still between phases. */
export function busy(view:UpdatesView):boolean{
 const request=view.request;
 if(request&&request.kind!=='check'&&(request.state==='requested'||request.state==='running'))return true;
 return view.last?.phase==='applied'||view.last?.phase==='rolling_back';
}

/** Twelve hour display of the API's "HH:MM" server time, by arithmetic rather than Intl so
 * the output is the same on every browser. */
export type ClockTime={hour:number;minute:number;period:'AM'|'PM'};
export function parseClock(value:string):ClockTime|undefined{
 const match=/^(\d{2}):(\d{2})$/.exec(value);if(!match)return undefined;
 const hours=Number(match[1]),minute=Number(match[2]);
 if(hours>23||minute>59)return undefined;
 return {hour:hours%12===0?12:hours%12,minute,period:hours<12?'AM':'PM'};
}
export function toClock24(time:ClockTime):string{
 const hours=(time.hour%12)+(time.period==='PM'?12:0);
 return String(hours).padStart(2,'0')+':'+String(time.minute).padStart(2,'0');
}
export function formatClock(value:string):string{
 const time=parseClock(value);
 return time?`${time.hour}:${String(time.minute).padStart(2,'0')} ${time.period}`:value;
}
export function describeWindow(window:{start:string;end:string}):string{
 return `Every day from ${formatClock(window.start)} to ${formatClock(window.end)}, server time.`;
}
export function windowError(window:{start:string;end:string}):string{
 if(!parseClock(window.start)||!parseClock(window.end))return 'Choose a start and an end time.';
 if(window.start===window.end)return 'The window needs different start and end times.';
 return '';
}

/** A point in time for the operator, always in twelve hour form. */
export function formatWhen(iso:string|null|undefined,locale?:string):string{
 if(!iso)return 'Never';
 const date=new Date(iso);if(Number.isNaN(date.getTime()))return iso;
 return date.toLocaleString(locale,{year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',hour12:true});
}

/** How a response to an update route is treated. While the server restarts the console
 * loses it: network errors, gateway errors and a briefly rejected token are "restarting",
 * never a failure. */
export type Outcome='ok'|'restarting'|'forbidden'|'refused'|'failed';
export function classifyStatus(status:number):Outcome{
 if(status>=200&&status<300)return 'ok';
 if(status===401||status===500||status===502||status===503||status===504)return 'restarting';
 if(status===403)return 'forbidden';
 if(status===400||status===409)return 'refused';
 return 'failed';
}

/** The progress view is a small state machine fed by each poll. */
export type WatchKind='apply'|'rollback'|'check';
export type Watch={
 kind:WatchKind;version?:string;since:number;
 baseline:{last:string|null;request:string|null;checkedAt:string|null};
 unreachable:number;phase:'waiting'|'restarting'|'done'|'timed_out';
 view?:UpdatesView;
};
export const POLL_MS=2000,MAX_BACKOFF_MS=15000;
const LIMIT_MS:Record<WatchKind,number>={apply:10*60*1000,rollback:10*60*1000,check:2*60*1000};
const FINAL:ReadonlySet<UpdatePhase>=new Set(['confirmed','rolled_back','rollback_failed','failed']);
const signature=(last:UpdateRecord|null|undefined)=>last?`${last.startedAt}|${last.phase}|${last.finishedAt??''}`:null;

/** Starts watching from what the console saw before the request. `resume` watches a request
 * that was already under way when the page loaded, so that request itself counts. */
export function startWatch(kind:WatchKind,view:UpdatesView|undefined,now:number,resume=false,version?:string):Watch{
 return {kind,version,since:now,unreachable:0,phase:'waiting',view,
  baseline:{last:signature(view?.last),request:resume?null:view?.request?.requestedAt??null,checkedAt:view?.checkedAt??null}};
}

/** What to watch on page load: an apply or rollback that is still under way. */
export function resumeKind(view:UpdatesView|undefined):WatchKind|undefined{
 if(!view)return undefined;
 const request=view.request;
 if(request&&request.kind!=='check'&&(request.state==='requested'||request.state==='running'))return request.kind;
 if(view.last?.phase==='applied')return 'apply';
 if(view.last?.phase==='rolling_back')return 'rollback';
 return undefined;
}

export type PollEvent={type:'ok';view:UpdatesView}|{type:'unreachable'};

/** One poll result in, the next state and the delay before the next poll out (null: stop). */
export function stepWatch(watch:Watch,event:PollEvent,now:number):{watch:Watch;delay:number|null}{
 if(watch.phase==='done'||watch.phase==='timed_out')return {watch,delay:null};
 const expired=now-watch.since>=LIMIT_MS[watch.kind];
 if(event.type==='unreachable'){
  const unreachable=watch.unreachable+1;
  if(expired)return {watch:{...watch,unreachable,phase:'timed_out'},delay:null};
  return {watch:{...watch,unreachable,phase:'restarting'},delay:Math.min(POLL_MS*2**Math.min(unreachable,4),MAX_BACKOFF_MS)};
 }
 const view=event.view,next:Watch={...watch,view,unreachable:0,phase:'waiting'};
 const request=view.request,fresh=request&&request.kind===watch.kind&&request.requestedAt!==watch.baseline.request;
 let finished=Boolean(fresh&&request?.state==='failed');
 if(watch.kind==='check')finished||=Boolean(fresh&&request?.state==='done')||view.checkedAt!==watch.baseline.checkedAt;
 else finished||=Boolean(view.last&&FINAL.has(view.last.phase)&&signature(view.last)!==watch.baseline.last);
 if(finished)return {watch:{...next,phase:'done'},delay:null};
 if(expired)return {watch:{...next,phase:'timed_out'},delay:null};
 return {watch:next,delay:POLL_MS};
}

export type Stage='queued'|'running'|'restarting'|'checking'|'returning'|'confirmed'|'rolled_back'|'rolled_back_by_request'|'rollback_failed'|'failed'|'refused'|'checked'|'timed_out';

/** Where a watched request stands, in the terms the progress view speaks. */
export function watchStage(watch:Watch):Stage{
 const view=watch.view,request=view?.request,last=view?.last;
 const fresh=request&&request.kind===watch.kind&&request.requestedAt!==watch.baseline.request;
 if(watch.phase==='timed_out')return 'timed_out';
 if(watch.phase==='done'){
  if(fresh&&request?.state==='failed')return 'refused';
  if(watch.kind==='check')return 'checked';
  if(last?.phase==='confirmed')return 'confirmed';
  if(last?.phase==='rolled_back')return last.automatic?'rolled_back':'rolled_back_by_request';
  if(last?.phase==='rollback_failed')return 'rollback_failed';
  return 'failed';
 }
 if(watch.phase==='restarting')return 'restarting';
 if(last?.phase==='rolling_back')return 'returning';
 if(watch.kind==='apply'&&last?.phase==='applied'&&(signature(last)!==watch.baseline.last||watch.baseline.request===null))return 'checking';
 if(fresh&&request?.state==='running')return 'running';
 return 'queued';
}

export const STAGE_TEXT:Record<Stage,string>={
 queued:'Waiting for the server to pick up the request.',
 running:'Taking a backup, then moving to the new version.',
 restarting:'The server is restarting. The console reconnects by itself; this can take a few minutes.',
 checking:'The new version started. Checking that it is healthy.',
 returning:'Returning to the previous version.',
 confirmed:'The update is installed and healthy.',
 rolled_back:'The update did not start, so Sbarbase is back on the previous version. Nothing was lost.',
 rolled_back_by_request:'Sbarbase is back on the previous version.',
 rollback_failed:'The previous version did not start either. Restore from the backups taken before the upgrade.',
 failed:'The update did not go ahead.',
 refused:'The server did not carry out the request.',
 checked:'The check finished.',
 timed_out:'No final outcome after 10 minutes. On the server, run python3 lab/upgrade.py status to see where it stands.'};
