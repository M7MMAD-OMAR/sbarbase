import {closeSync,chmodSync,fsyncSync,linkSync,mkdirSync,openSync,renameSync,unlinkSync,writeSync} from 'node:fs';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {readJsonCached} from '../http/cached-json';

/** The console's side of the update channel. The console process cannot run an upgrade (it
 * would move the checkout under itself and cannot restart itself), so it only reads what the
 * supervisor and the release channel wrote, saves the operator's settings, and asks: one
 * request at a time, in request.json, which the supervisor (lab/dev.py schedule_updates, with
 * lab/updates.py) checks again and carries out. The file formats are described there.
 *
 * The supervisor is the one authority on whether a release can be installed now: current.json
 * carries its `apply` verdict, and the console answers with it rather than judging again. */
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
/** Whether the release on offer can be installed now, and the sentence when it cannot;
 * `acknowledgement`: the operator confirms its warning first. Null when nothing is on offer. */
type Install={possible:boolean;reason:string|null;acknowledgement:boolean};
export type UpdatesView={current:{version:string;commit:string};available:null|Available;refusals:string[];
  skipped:string[];newest:null|Newest;checkedAt:string|null;checkError:string|null;settings:UpdateSettings;timezone:TimeZone;
  last:null|Last;request:null|RequestView;install:null|Install;canRollback:boolean};

export const DEFAULT_SETTINGS:UpdateSettings={check:true,automatic:false,window:{start:'03:00',end:'05:00'}};
const CLOCK=/^([01]\d|2[0-3]):[0-5]\d$/;
const PHASES:Phase[]=['applied','confirmed','rolling_back','rolled_back','rollback_failed','failed'];
/** The phases in which an upgrade still waits for its health checks or its way back. */
export const PENDING:readonly string[]=['applied','rolling_back'];
/** The sentences shown to the operator as they are. lab/updates.py MESSAGES holds the same
 * words: lab/test_updates.py checks that each of its sentences is here, and
 * tests/updates-routes.test.ts that each of these is there. Most apply refusals now reach the
 * console as the supervisor's own `apply.reason`. */
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
  settings_fields:'Settings need exactly check, automatic and window.',
  settings_switches:'Check and automatic must each be on or off.',
  settings_window:'The maintenance window needs a start and an end.',
  settings_clock:'The maintenance window needs a valid start and end time.',
  settings_same:'The maintenance window needs different start and end times.',
  settings_check:'Automatic updates need checking for new releases turned on.',
} as const;
/** The console's own sentences, for what only it sees: the supervisor has not written its
 * verdict yet. */
export const CONSOLE_MESSAGES={
  starting:'The server has not finished starting; try again in a minute.',
  unjudged:'Sbarbase has not checked yet whether this release can be installed. Try again in a moment.',
  rollback_unjudged:'Sbarbase has not checked yet whether this update can be rolled back. Try again in a moment.',
} as const;

/** A refusal the updates routes answer with its own sentence: 400 for the input, 409 for the
 * state. Nothing was changed. */
export class UpdateRefusal extends Error {
  constructor(message:string,readonly status:400|409=409) {super(message);}
}

type Json=Record<string,unknown>;
const isObject=(value:unknown):value is Json=>!!value&&typeof value==='object'&&!Array.isArray(value);
const text=(value:unknown):value is string=>typeof value==='string';
const texts=(value:unknown):string[]=>Array.isArray(value)?value.filter(text):[];

/** A file of the directory as an object, or null when it is missing or not a JSON object. An
 * unchanged file costs one stat (readJsonCached); the value is shared, so it is never changed. */
function read(directory:string,name:string):Json|null {
  try {const value=readJsonCached(join(directory,name));return isObject(value)?value:null;}
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
  if(!isObject(value)||Object.keys(value).sort().join()!=='automatic,check,window')return UPDATE_MESSAGES.settings_fields;
  if(typeof value.check!=='boolean'||typeof value.automatic!=='boolean')return UPDATE_MESSAGES.settings_switches;
  const window=value.window;
  if(!isObject(window)||Object.keys(window).sort().join()!=='end,start')return UPDATE_MESSAGES.settings_window;
  if(!text(window.start)||!text(window.end)||!CLOCK.test(window.start)||!CLOCK.test(window.end))return UPDATE_MESSAGES.settings_clock;
  if(window.start===window.end)return UPDATE_MESSAGES.settings_same;
  if(value.automatic&&!value.check)return UPDATE_MESSAGES.settings_check;
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

/** What runs now: the supervisor's current.json, else the checkout's release.json without a
 * commit. */
function current(recorded:Json|null,root:string):{version:string;commit:string} {
  if(recorded&&text(recorded.version)&&text(recorded.commit))return {version:recorded.version,commit:recorded.commit};
  const release=read(root,'release.json');
  return {version:release&&text(release.version)?release.version:'unknown',commit:''};
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
/** This process's own zone, shown only until the supervisor has published the one it reads the
 * window in. Both read the same TZ, and the image carries tzdata, so they name the same zone. */
function processZone():TimeZone {
  const minutes=-new Date().getTimezoneOffset(),size=Math.abs(minutes);
  const offset=`${minutes<0?'-':'+'}${String(Math.floor(size/60)).padStart(2,'0')}:${String(size%60).padStart(2,'0')}`;
  return {name:Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC',offset};
}

/** The time zone the maintenance window is read in: the supervisor's, from current.json. */
export function serverZone(directory=UPDATES_DIRECTORY,recorded=read(directory,'current.json')):TimeZone {
  const zone=recorded?.timezone;
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

/** A request the supervisor has not finished: while it holds the slot, no other can be recorded. */
function open(request:RequestView|null) {return !!request&&(request.state==='requested'||request.state==='running');}
/** An apply or rollback under way. A check holds the slot only for moments, so the page still
 * offers "Install update" meanwhile. */
function underway(request:RequestView|null) {return open(request)&&request?.kind!=='check';}

/** Whether "roll back" is offered, and the sentence when it is not. The supervisor judged it with
 * upgrade.py's own rollback_refusal and recorded the verdict for this very upgrade record. */
function rollbackVerdict(recorded:Json|null,state:Json|null):{possible:boolean;reason:string} {
  if(!state||state.phase!=='confirmed')return {possible:false,reason:UPDATE_MESSAGES.no_rollback};
  const verdict=recorded?.rollback;
  if(!isObject(verdict)||verdict.started_at!==state.started_at)return {possible:false,reason:CONSOLE_MESSAGES.rollback_unjudged};
  if(verdict.possible!==true)return {possible:false,reason:text(verdict.reason)?verdict.reason:UPDATE_MESSAGES.no_rollback};
  return {possible:true,reason:''};
}

type ApplyVerdict={version:string;tag:string;possible:boolean;reason:string|null;acknowledgement:boolean};
/** The supervisor's `apply` verdict from current.json: undefined when it wrote none (the server
 * is still starting), null when it has nothing to install. */
function applyVerdict(recorded:Json|null):ApplyVerdict|null|undefined {
  const apply=recorded?.apply;
  if(apply===null)return null;
  if(!isObject(apply)||!text(apply.version)||!text(apply.tag))return undefined;
  return {version:apply.version,tag:apply.tag,possible:apply.possible===true,reason:text(apply.reason)?apply.reason:null,
    acknowledgement:apply.acknowledgement===true};
}

/** "Install update" for the release on offer, from the supervisor's verdict about that very
 * version. An apply or rollback already under way holds the request slot. */
function install(release:Available|null,verdict:ApplyVerdict|null|undefined,request:RequestView|null):Install|null {
  if(!release)return null;
  if(verdict===undefined)return {possible:false,reason:CONSOLE_MESSAGES.starting,acknowledgement:false};
  if(!verdict||verdict.version!==release.version)return {possible:false,reason:CONSOLE_MESSAGES.unjudged,acknowledgement:false};
  const acknowledgement=verdict.acknowledgement;
  if(!verdict.possible)return {possible:false,reason:verdict.reason??UPDATE_MESSAGES.refused,acknowledgement};
  if(underway(request))return {possible:false,reason:UPDATE_MESSAGES.busy,acknowledgement};
  return {possible:true,reason:null,acknowledgement};
}

/** GET /management/v1/updates. `root` is the checkout, whose release.json names the version
 * when the supervisor has not published one. The supervisor keeps available.json about the
 * running commit (it moves a result made on another one aside), so it is shown as it is. */
export function updatesView(directory=UPDATES_DIRECTORY,root='.'):UpdatesView {
  const recorded=read(directory,'current.json'),document=read(directory,'available.json'),state=read(directory,'state.json');
  const request=requestView(read(directory,'request.json'))??requestView(read(directory,'last-request.json'));
  const check=read(directory,'check.json'),release=available(document?.available);
  return {current:current(recorded,root),available:release,refusals:texts(document?.refusals),
    skipped:texts(document?.skipped),newest:newest(document?.newest),
    checkedAt:document&&text(document.checked_at)?document.checked_at:null,
    checkError:check&&text(check.error)?check.error:null,settings:readSettings(directory),timezone:serverZone(directory,recorded),
    last:last(state),request,install:install(release,applyVerdict(recorded),request),
    canRollback:rollbackVerdict(recorded,state).possible&&!open(request)};
}

export type UpdateRequestKind='apply'|'rollback'|'check';
/** Asks the supervisor for a check, an apply or a rollback, or throws UpdateRefusal (409) with
 * the sentence. An apply goes ahead only on the supervisor's own verdict for that version, which
 * it judges once more before it runs, and names the tag that verdict names. request.json is
 * linked into place, which fails while another request holds the slot, so two requests can
 * never both be recorded. An apply that needs the acknowledgement records it for the supervisor. */
export function requestUpdate(kind:UpdateRequestKind,version?:string,directory=UPDATES_DIRECTORY,acknowledged=false):void {
  let fields:{version?:string;tag?:string;acknowledged?:true}={};
  if(kind==='apply') {
    const verdict=applyVerdict(read(directory,'current.json'));
    if(verdict===undefined)throw new UpdateRefusal(CONSOLE_MESSAGES.starting);
    if(verdict===null)throw new UpdateRefusal(UPDATE_MESSAGES.nothing);
    if(verdict.version!==version)throw new UpdateRefusal(UPDATE_MESSAGES.other);
    if(!verdict.possible)throw new UpdateRefusal(verdict.reason??UPDATE_MESSAGES.refused);
    if(verdict.acknowledgement&&!acknowledged)throw new UpdateRefusal(UPDATE_MESSAGES.acknowledge);
    fields={version:verdict.version,tag:verdict.tag,...(verdict.acknowledgement?{acknowledged:true as const}:{})};
  }
  if(kind==='rollback') {
    const verdict=rollbackVerdict(read(directory,'current.json'),read(directory,'state.json'));
    if(!verdict.possible)throw new UpdateRefusal(verdict.reason);
  }
  const request={id:randomUUID(),kind,trigger:'console',state:'requested',
    requested_at:new Date().toISOString().replace(/\.\d{3}Z$/,'+00:00'),...fields};
  privateDirectory(directory);
  const path=temporary(directory,request);
  try {linkSync(path,join(directory,'request.json'));}
  catch(error) {
    if((error as {code?:string}).code==='EEXIST')throw new UpdateRefusal(UPDATE_MESSAGES.busy);
    throw error;
  }
  finally {try{unlinkSync(path);}catch{}}
}
