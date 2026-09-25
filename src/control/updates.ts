import {closeSync,chmodSync,fsyncSync,linkSync,mkdirSync,openSync,readFileSync,renameSync,unlinkSync,writeSync} from 'node:fs';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';

/** The console's side of the update channel. The console process cannot run an upgrade (it
 * would move the checkout under itself and cannot restart itself), so it only reads what the
 * supervisor and the release channel wrote, saves the operator's settings, and asks: one
 * request at a time, in request.json, which the supervisor (lab/dev.py schedule_updates, with
 * lab/updates.py) checks again and carries out. The file formats are described there. */
export const UPDATES_DIRECTORY='.lab/upgrades';

export type UpdateSettings={check:boolean;automatic:boolean;window:{start:string;end:string}};
/** `attended`: the Auth, Storage or Realtime image changes, and each migrates the environment
 * databases when it starts. The operator may install it after acknowledging that a way back may
 * need the environment backups; it is never installed automatically (lab/release_channel.py). */
export type UpdateClass='safe'|'attended'|'rebuild'|'manual';
const CLASSES:UpdateClass[]=['safe','attended','rebuild','manual'];
type Change={label:string;before:string;after:string};
type Available={version:string;tag:string;commit:string;class:UpdateClass;signed:boolean;reasons:string[];
  notes:{en:string;ar:string};changes:Change[]};
/** The newest release the check passed over, and why: shown as information. */
type Newest={version:string;tag:string;class:UpdateClass|null;signed:boolean;reasons:string[]};
/** The zone of the clock the supervisor reads the maintenance window in. */
export type TimeZone={name:string;offset:string};
type Phase='applied'|'confirmed'|'rolling_back'|'rolled_back'|'rollback_failed'|'failed';
type Last={phase:Phase;from:string;to:string;version?:string;startedAt:string;finishedAt?:string;automatic:boolean;
  failure?:string;trigger?:'cli'|'console'|'automatic'};
type RequestView={kind:'apply'|'rollback'|'check';version?:string;state:'requested'|'running'|'done'|'failed';
  requestedAt:string;detail?:string};
export type UpdatesView={current:{version:string;commit:string};available:null|Available;refusals:string[];
  skipped:string[];newest:null|Newest;checkedAt:string|null;checkError:string|null;settings:UpdateSettings;timezone:TimeZone;
  last:null|Last;request:null|RequestView;canRollback:boolean};

export const DEFAULT_SETTINGS:UpdateSettings={check:true,automatic:false,window:{start:'03:00',end:'05:00'}};
const CLOCK=/^([01]\d|2[0-3]):[0-5]\d$/;
const PHASES:Phase[]=['applied','confirmed','rolling_back','rolled_back','rollback_failed','failed'];
const PENDING:Phase[]=['applied','rolling_back'];
/** The sentences a refusal answers with, shown to the operator as they are. lab/updates.py
 * MESSAGES holds the same words; lab/test_updates.py keeps the two in step. */
export const UPDATE_MESSAGES={
  busy:'Another update request is still in progress. Wait for it to finish.',
  nothing:'No newer release is available to install. Check for updates first.',
  stale:'The last check was made on another version of Sbarbase. Check for updates again.',
  other:'That version is not the release available now. Check for updates again.',
  class:'This release cannot be installed from the console. Follow the instructions on the Updates page to install it on the server.',
  acknowledge:'This release updates services that change environment databases when they start. Confirm the warning on the Updates page to install it.',
  unsigned:'This release is not signed by a Sbarbase release key, so it cannot be installed.',
  refused:'The server cannot install this release now. The reasons are listed on the Updates page.',
  pending:'The last update has not finished starting yet. Wait for it to finish.',
  no_rollback:'There is no installed update to roll back.',
} as const;

type Json=Record<string,unknown>;
const isObject=(value:unknown):value is Json=>!!value&&typeof value==='object'&&!Array.isArray(value);
const text=(value:unknown):value is string=>typeof value==='string';
const texts=(value:unknown):string[]=>Array.isArray(value)?value.filter(text):[];

/** A file of the directory as an object, or null when it is missing or not a JSON object. */
function read(directory:string,name:string):Json|null {
  try {const value:unknown=JSON.parse(readFileSync(join(directory,name),'utf8'));return isObject(value)?value:null;}
  catch {return null;}
}

function privateDirectory(directory:string) {
  mkdirSync(directory,{recursive:true,mode:0o700});
  try {chmodSync(directory,0o700);} catch {}
}

/** A private (0600) temporary file with the JSON value, flushed to disk; returns its path. */
function temporary(directory:string,value:unknown):string {
  const path=join(directory,`.console.${randomUUID()}.tmp`);
  const handle=openSync(path,'wx',0o600);
  try {writeSync(handle,JSON.stringify(value));fsyncSync(handle);}
  finally {closeSync(handle);}
  return path;
}

/** The settings exactly as the console sends them, or the sentence that says what is wrong. */
export function validateSettings(value:unknown):UpdateSettings|string {
  if(!isObject(value)||Object.keys(value).sort().join()!=='automatic,check,window')return 'Settings need exactly check, automatic and window.';
  if(typeof value.check!=='boolean'||typeof value.automatic!=='boolean')return 'Check and automatic must each be on or off.';
  const window=value.window;
  if(!isObject(window)||Object.keys(window).sort().join()!=='end,start')return 'The maintenance window needs a start and an end.';
  if(!text(window.start)||!text(window.end)||!CLOCK.test(window.start)||!CLOCK.test(window.end))
    return 'The maintenance window needs a valid start and end time.';
  if(window.start===window.end)return 'The maintenance window needs different start and end times.';
  if(value.automatic&&!value.check)return 'Automatic updates need checking for new releases turned on.';
  return {check:value.check,automatic:value.automatic,window:{start:window.start,end:window.end}};
}

export function readSettings(directory=UPDATES_DIRECTORY):UpdateSettings {
  const settings=validateSettings(read(directory,'settings.json'));
  return typeof settings==='string'?structuredClone(DEFAULT_SETTINGS):settings;
}

/** Replaces settings.json atomically: private temporary file, fsync, rename. */
export function saveSettings(settings:UpdateSettings,directory=UPDATES_DIRECTORY):UpdateSettings {
  privateDirectory(directory);
  const path=temporary(directory,settings);
  try {renameSync(path,join(directory,'settings.json'));}
  catch(error) {try{unlinkSync(path);}catch{} throw error;}
  return settings;
}

/** What runs now: the supervisor's current.json, else what the last check recorded, else the
 * checkout's release.json without a commit. */
function current(directory:string,root:string):{version:string;commit:string} {
  const recorded=read(directory,'current.json');
  if(recorded&&text(recorded.version)&&text(recorded.commit))return {version:recorded.version,commit:recorded.commit};
  const checked=read(directory,'available.json')?.current;
  if(isObject(checked)&&text(checked.version)&&text(checked.commit))return {version:checked.version,commit:checked.commit};
  const release=read(root,'release.json');
  return {version:release&&text(release.version)?release.version:'unknown',commit:''};
}

/** The last check, when it was made on the running commit. Right after an upgrade it still
 * names the release just installed, so it counts only once a check ran on this version. */
function checked(directory:string,running:{commit:string}):Json|null {
  const document=read(directory,'available.json');
  if(!document)return null;
  const madeOn=document.current;
  if(running.commit&&!(isObject(madeOn)&&madeOn.commit===running.commit))return null;
  return document;
}

function available(value:unknown):Available|null {
  if(!isObject(value)||!text(value.version)||!text(value.tag)||!text(value.commit)||!isObject(value.notes))return null;
  if(!CLASSES.includes(value.class as UpdateClass))return null;
  const changes=Array.isArray(value.changes)?value.changes.filter(isObject).map(row=>({
    label:String(row.image??''),before:String(row.from??'none'),after:String(row.to??'none')})):[];
  return {version:value.version,tag:value.tag,commit:value.commit,class:value.class as Available['class'],signed:value.signed===true,
    reasons:texts(value.reasons),notes:{en:text(value.notes.en)?value.notes.en:'',ar:text(value.notes.ar)?value.notes.ar:''},changes};
}

function newest(value:unknown):Newest|null {
  if(!isObject(value)||!text(value.version)||!text(value.tag))return null;
  return {version:value.version,tag:value.tag,class:CLASSES.includes(value.class as UpdateClass)?value.class as UpdateClass:null,
    signed:value.signed===true,reasons:texts(value.reasons)};
}

const OFFSET=/^[+-]\d{2}:\d{2}$/;
/** This process's own zone, used only until the supervisor has published the one it reads the
 * window in: Bun carries its own zone data, so a TZ name it resolves may be one the supervisor
 * cannot (lab/updates.py zone). */
function processZone():TimeZone {
  const minutes=-new Date().getTimezoneOffset(),size=Math.abs(minutes);
  const offset=`${minutes<0?'-':'+'}${String(Math.floor(size/60)).padStart(2,'0')}:${String(size%60).padStart(2,'0')}`;
  let name='UTC';
  try {name=Intl.DateTimeFormat().resolvedOptions().timeZone||name;} catch {}
  return {name,offset};
}

/** The time zone the maintenance window is read in: the supervisor's, from current.json. */
export function serverZone(directory=UPDATES_DIRECTORY):TimeZone {
  const zone=read(directory,'current.json')?.timezone;
  if(isObject(zone)&&text(zone.name)&&zone.name.length<=64&&text(zone.offset)&&OFFSET.test(zone.offset))return {name:zone.name,offset:zone.offset};
  return processZone();
}

function last(state:Json|null):Last|null {
  if(!state||!PHASES.includes(state.phase as Phase)||!text(state.from)||!text(state.to)||!text(state.started_at))return null;
  const release=isObject(state.release)&&text(state.release.version)?state.release.version:undefined;
  const trigger=['cli','console','automatic'].includes(state.trigger as string)?state.trigger as Last['trigger']:undefined;
  return {phase:state.phase as Phase,from:state.from,to:state.to,...(release?{version:release}:{}),startedAt:state.started_at,
    ...(text(state.finished_at)?{finishedAt:state.finished_at}:{}),automatic:state.automatic===true,
    ...(text(state.failure)?{failure:state.failure}:{}),...(trigger?{trigger}:{})};
}

function requestView(value:Json|null):RequestView|null {
  if(!value||!['apply','rollback','check'].includes(value.kind as string)||!text(value.requested_at))return null;
  if(!['requested','running','done','failed'].includes(value.state as string))return null;
  return {kind:value.kind as RequestView['kind'],...(text(value.version)?{version:value.version}:{}),state:value.state as RequestView['state'],
    requestedAt:value.requested_at,...(text(value.detail)?{detail:value.detail}:{})};
}

function open(request:RequestView|null) {return !!request&&(request.state==='requested'||request.state==='running');}

/** Whether "roll back" is offered, and the sentence when it is not. The supervisor judged it with
 * upgrade.py's own rollback_refusal and recorded the verdict for this very upgrade record. */
function rollbackVerdict(directory:string,state:Json|null,request:RequestView|null):{possible:boolean;reason:string} {
  if(!state||state.phase!=='confirmed')return {possible:false,reason:UPDATE_MESSAGES.no_rollback};
  if(open(request))return {possible:false,reason:UPDATE_MESSAGES.busy};
  const verdict=read(directory,'current.json')?.rollback;
  if(!isObject(verdict)||verdict.started_at!==state.started_at)
    return {possible:false,reason:'Sbarbase has not checked yet whether this update can be rolled back. Try again in a moment.'};
  if(verdict.possible!==true)return {possible:false,reason:text(verdict.reason)?verdict.reason:UPDATE_MESSAGES.no_rollback};
  return {possible:true,reason:''};
}

/** GET /management/v1/updates. `root` is the checkout, whose release.json names the version
 * when the supervisor has not published one. */
export function updatesView(directory=UPDATES_DIRECTORY,root='.'):UpdatesView {
  const running=current(directory,root),document=checked(directory,running),state=read(directory,'state.json');
  const request=requestView(read(directory,'request.json'))??requestView(read(directory,'last-request.json'));
  const check=read(directory,'check.json');
  return {current:running,available:available(document?.available),refusals:texts(document?.refusals),
    skipped:texts(document?.skipped),newest:newest(document?.newest),
    checkedAt:document&&text(document.checked_at)?document.checked_at:null,
    checkError:check&&text(check.error)?check.error:null,settings:readSettings(directory),timezone:serverZone(directory),
    last:last(state),request,canRollback:rollbackVerdict(directory,state,request).possible};
}

/** Why an apply of `version` is refused, or null. The supervisor asks the same again, and
 * `upgrade.py start --release` verifies the release once more from its source. An `attended`
 * release needs the operator's acknowledgement of its warning. Notes about releases the check
 * passed over (`skipped`) never refuse the one on offer. */
function applyRefusal(directory:string,root:string,version:string,acknowledged:boolean):string|null {
  const running=current(directory,root),state=read(directory,'state.json');
  if(state&&PENDING.includes(state.phase as Phase))return UPDATE_MESSAGES.pending;
  const document=read(directory,'available.json');
  if(!document||!isObject(document.available))return UPDATE_MESSAGES.nothing;
  if(!checked(directory,running))return UPDATE_MESSAGES.stale;
  const release=document.available;
  if(release.version!==version)return UPDATE_MESSAGES.other;
  if(release.class!=='safe'&&release.class!=='attended')return UPDATE_MESSAGES.class;
  if(release.class==='attended'&&!acknowledged)return UPDATE_MESSAGES.acknowledge;
  if(release.signed!==true)return UPDATE_MESSAGES.unsigned;
  if(texts(document.refusals).length)return UPDATE_MESSAGES.refused;
  return null;
}

export type UpdateRequestKind='apply'|'rollback'|'check';
/** Asks the supervisor for a check, an apply or a rollback. Returns null when the request was
 * recorded, or the sentence of a refusal (409). request.json is linked into place, which fails
 * while another request holds the slot, so two requests can never both be recorded. An apply
 * of an `attended` release records the operator's acknowledgement for the supervisor. */
export function requestUpdate(kind:UpdateRequestKind,version?:string,directory=UPDATES_DIRECTORY,root='.',acknowledged=false):string|null {
  if(open(requestView(read(directory,'request.json'))))return UPDATE_MESSAGES.busy;
  if(kind==='apply'){const refusal=applyRefusal(directory,root,version??'',acknowledged);if(refusal)return refusal;}
  if(kind==='rollback'){
    const verdict=rollbackVerdict(directory,read(directory,'state.json'),null);
    if(!verdict.possible)return verdict.reason;
  }
  const document=read(directory,'available.json'),release=isObject(document?.available)?document.available:null;
  const request={id:randomUUID(),kind,trigger:'console',state:'requested',requested_at:new Date().toISOString().replace(/\.\d{3}Z$/,'+00:00'),
    ...(kind==='apply'&&version?{version,tag:release&&text(release.tag)?release.tag:'v'+version}:{}),
    ...(kind==='apply'&&release?.class==='attended'&&acknowledged?{acknowledged:true}:{})};
  privateDirectory(directory);
  const path=temporary(directory,request);
  try {linkSync(path,join(directory,'request.json'));}
  catch(error) {
    if((error as {code?:string}).code==='EEXIST')return UPDATE_MESSAGES.busy;
    throw error;
  }
  finally {try{unlinkSync(path);}catch{}}
  return null;
}
