import {test,expect,describe} from 'bun:test';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {ACKNOWLEDGEMENT,CLASS_WORDS,STAGE_TEXT,availableText,banners,newestText,classifyStatus,describeWindow,dismiss,formatClock,formatWhen,installState,installable,openRequest,parseClock,readDismissed,
 releaseNotes,settingsKey,startWatch,stepWatch,toClock24,watchStage,windowError,zoneText,type UpdatesView,type Watch} from '../ui/releases';

const ui=join(import.meta.dir,'..','ui');
const app=readFileSync(join(ui,'App.tsx'),'utf8');
const page=readFileSync(join(ui,'Updates.tsx'),'utf8');
const logic=readFileSync(join(ui,'releases.ts'),'utf8');
const api=readFileSync(join(ui,'api.ts'),'utf8');

const release={version:'0.2.0',tag:'v0.2.0',commit:'b'.repeat(40),class:'safe' as const,signed:true,reasons:['Only PostgREST changes.'],
 notes:{en:'Faster REST.',ar:'واجهة REST أسرع.'},changes:[{label:'PostgREST',before:'v12',after:'v13'}]};
function view(change:Partial<UpdatesView>={}):UpdatesView{
 return {current:{version:'0.1.0',commit:'a'.repeat(40)},available:release,refusals:[],skipped:[],newest:null,checkedAt:'2026-09-25T10:00:00Z',checkError:null,
  settings:{check:true,automatic:false,window:{start:'02:00',end:'05:00'}},timezone:{name:'UTC',offset:'+00:00'},last:null,request:null,
  install:{possible:true,reason:null,acknowledgement:false},canRollback:false,...change};
}
const at=(minutes:number)=>1_000_000+minutes*60_000;

describe('twelve hour clock',()=>{
 test('the edges of the day convert both ways',()=>{
  expect(formatClock('00:00')).toBe('12:00 AM');
  expect(formatClock('00:30')).toBe('12:30 AM');
  expect(formatClock('01:05')).toBe('1:05 AM');
  expect(formatClock('11:59')).toBe('11:59 AM');
  expect(formatClock('12:00')).toBe('12:00 PM');
  expect(formatClock('12:30')).toBe('12:30 PM');
  expect(formatClock('15:00')).toBe('3:00 PM');
  expect(formatClock('23:59')).toBe('11:59 PM');
  expect(toClock24({hour:12,minute:0,period:'AM'})).toBe('00:00');
  expect(toClock24({hour:12,minute:15,period:'PM'})).toBe('12:15');
  expect(toClock24({hour:3,minute:0,period:'PM'})).toBe('15:00');
 });
 test('every minute of the day round-trips',()=>{
  for(let minutes=0;minutes<24*60;minutes++){
   const value=String(Math.floor(minutes/60)).padStart(2,'0')+':'+String(minutes%60).padStart(2,'0');
   const time=parseClock(value);
   expect(time).toBeDefined();expect(toClock24(time!)).toBe(value);
   expect(formatClock(value)).toMatch(/^(1[0-2]|[1-9]):[0-5]\d (AM|PM)$/);
  }
 });
 test('malformed values are refused, and the window is described in server time',()=>{
  for(const value of ['24:00','7:00','12:60','','noon'])expect(parseClock(value)).toBeUndefined();
  expect(describeWindow({start:'23:00',end:'03:30'})).toBe('Every day from 11:00 PM to 3:30 AM, server time.');
  expect(describeWindow({start:'03:00',end:'05:00'},{name:'Asia/Dubai',offset:'+04:00'})).toBe('Every day from 3:00 AM to 5:00 AM, server time (Asia/Dubai, UTC+04:00).');
  expect(describeWindow({start:'03:00',end:'05:00'},{name:'UTC',offset:'+00:00'})).toBe('Every day from 3:00 AM to 5:00 AM, server time (UTC).');
  expect(windowError({start:'02:00',end:'02:00'})).not.toBe('');
  expect(windowError({start:'02:00',end:'bad'})).not.toBe('');
  expect(windowError({start:'23:00',end:'01:00'})).toBe('');
 });
 test('a point in time is shown in twelve hour form',()=>{
  const text=formatWhen('2026-09-25T15:04:00Z','en-US').replace(/\s/g,' ');
  expect(text).toMatch(/\d{1,2}:04 (AM|PM)/);
  expect(formatWhen(null)).toBe('Never');
 });
 test('the server time zone is named as the supervisor reports it',()=>{
  expect(zoneText({name:'UTC',offset:'+00:00'})).toBe('UTC');
  expect(zoneText({name:'Asia/Dubai',offset:'+04:00'})).toBe('Asia/Dubai, UTC+04:00');
  // A fixed offset in TZ has no name of its own, only an abbreviation like "+04".
  expect(zoneText({name:'+04',offset:'+04:00'})).toBe('UTC+04:00');
  expect(zoneText({name:'America/St_Johns',offset:'-02:30'})).toBe('America/St_Johns, UTC-02:30');
  expect(zoneText({name:'Europe/London',offset:'+00:00'})).toBe('Europe/London, UTC');
  expect(page).toContain('describeWindow(form.window,timezone)');
 });
});

describe('settings form',()=>{
 test('an equal settings object from a poll does not reset the form; a saved change does',()=>{
  const saved={check:true,automatic:true,window:{start:'02:00',end:'05:00'}};
  expect(settingsKey({...saved,window:{...saved.window}})).toBe(settingsKey(saved));
  expect(settingsKey({...saved,automatic:false})).not.toBe(settingsKey(saved));
  expect(settingsKey({...saved,window:{start:'02:00',end:'06:00'}})).not.toBe(settingsKey(saved));
  expect(page).toContain('useEffect(()=>setForm(settings),[settingsKey(settings)])');
 });
 test('a confirmed upgrade asks for a reload from the banner as well as the page',()=>{
  expect(page).toContain("watchStage(watch)==='confirmed'");
  expect(page.split('Reload console').length-1).toBe(2);
 });
});

describe('release wording',()=>{
 test('each class names its status in words',()=>{
  expect(availableText({version:'1.2.0',class:'safe'})).toBe('Sbarbase 1.2.0 ready to install.');
  expect(availableText({version:'1.2.0',class:'rebuild'})).toBe('Sbarbase 1.2.0 needs a manual rebuild.');
  expect(availableText({version:'1.2.0',class:'manual'})).toBe('Sbarbase 1.2.0 needs a manual migration.');
  expect(availableText({version:'1.2.0',class:'attended'})).toBe('Sbarbase 1.2.0 ready to install after you confirm.');
  expect(CLASS_WORDS.attended.explanation).toContain('never installed automatically');
  expect(CLASS_WORDS.attended.explanation).toContain('backups taken before the update');
  expect(CLASS_WORDS.safe.explanation).not.toContain('pinned service images only');
  for(const words of Object.values(CLASS_WORDS)){expect(words.label.length).toBeGreaterThan(0);expect(words.explanation.length).toBeGreaterThan(40);}
 });
 test('notes follow the console language and fall back to English',()=>{
  expect(releaseNotes(release.notes,'ar')).toEqual({text:'واجهة REST أسرع.',language:'ar'});
  expect(releaseNotes(release.notes,'en')).toEqual({text:'Faster REST.',language:'en'});
  expect(releaseNotes({en:'Only English.',ar:'  '},'ar-SA')).toEqual({text:'Only English.',language:'en'});
 });
});

describe('banners',()=>{
 test('an available release shows until dismissed for that version',()=>{
  expect(banners(view(),[])).toEqual([{kind:'available',key:'available:0.2.0',version:'0.2.0',class:'safe',dismissible:true}]);
  expect(banners(view(),['available:0.2.0'])).toEqual([]);
  // A newer release is a new notice even after the older one was dismissed.
  expect(banners(view({available:{...release,version:'0.3.0'}}),['available:0.2.0'])).toHaveLength(1);
  expect(banners(undefined,[])).toEqual([]);
  expect(banners(view({available:null}),[])).toEqual([]);
 });
 test('a finished upgrade reports its outcome; the automatic way back only',()=>{
  const last={from:'a'.repeat(40),to:'b'.repeat(40),version:'0.2.0',startedAt:'2026-09-25T03:00:00Z',automatic:false};
  expect(banners(view({available:null,last:{...last,phase:'confirmed'}}),[])[0]).toMatchObject({kind:'confirmed',version:'0.2.0'});
  expect(banners(view({available:null,last:{...last,phase:'rolled_back',automatic:true}}),[])[0]).toMatchObject({kind:'rolled_back'});
  // An operator who rolled back asked for it; no notice.
  expect(banners(view({available:null,last:{...last,phase:'rolled_back',automatic:false}}),[])).toEqual([]);
  expect(banners(view({available:null,last:{...last,phase:'applied'}}),[])).toEqual([]);
  const confirmed=banners(view({available:null,last:{...last,phase:'confirmed'}}),[])[0]!;
  expect(banners(view({available:null,last:{...last,phase:'confirmed'}}),[confirmed.key])).toEqual([]);
 });
 test('with nothing installable, a newer signed release still earns a notice; an unsigned one does not',()=>{
  const manual={version:'0.4.0',tag:'v0.4.0',class:'manual' as const,signed:true,reasons:['It changes the PostgreSQL image']};
  expect(banners(view({available:null,newest:manual}),[])).toEqual([{kind:'newest',key:'available:0.4.0',version:'0.4.0',class:'manual',dismissible:true}]);
  expect(banners(view({available:null,newest:manual}),['available:0.4.0'])).toEqual([]);
  expect(banners(view({available:null,newest:{...manual,signed:false}}),[])).toEqual([]);
  // A release on offer is the notice; the newest one is on the page.
  expect(banners(view({newest:manual}),[]).map(banner=>banner.kind)).toEqual(['available']);
  expect(newestText(manual)).toBe('Sbarbase 0.4.0 needs a manual migration.');
  expect(newestText({version:'0.4.0',class:'safe'})).toBe('Sbarbase 0.4.0 is released, but this installation cannot install it yet.');
  expect(page).toContain("banner.kind==='newest'");
 });
 test('a failed way back is critical, first and cannot be dismissed',()=>{
  const failed=view({last:{phase:'rollback_failed',from:'a',to:'b',startedAt:'s',automatic:true}});
  const shown=banners(failed,['last:s:rollback_failed']);
  expect(shown[0]).toEqual({kind:'rollback_failed',key:'last:s:rollback_failed',dismissible:false});
  expect(shown[1]?.kind).toBe('available');
 });
 test('dismissals survive in storage and a broken storage never throws',()=>{
  const memory=new Map<string,string>();
  const store={getItem:(key:string)=>memory.get(key)??null,setItem:(key:string,value:string)=>void memory.set(key,value)};
  expect(readDismissed(store)).toEqual([]);
  expect(dismiss(store,'available:0.2.0')).toEqual(['available:0.2.0']);
  expect(dismiss(store,'available:0.2.0')).toEqual(['available:0.2.0']);
  expect(readDismissed(store)).toEqual(['available:0.2.0']);
  const broken={getItem:()=>{throw new Error('blocked');},setItem:()=>{throw new Error('blocked');}};
  expect(readDismissed(broken)).toEqual([]);
  expect(dismiss(broken,'x')).toEqual(['x']);
  expect(readDismissed(undefined)).toEqual([]);
  memory.set('sbarbase.updates.dismissed','{"not":"a list"}');expect(readDismissed(store)).toEqual([]);
 });
});

describe('install button',()=>{
 test('enabled exactly as the server judged it, with the server\'s own sentence when it is not',()=>{
  expect(installState(view())).toEqual({enabled:true,reasons:[]});
  const refused='This release is not signed by a Sbarbase release key, so it cannot be installed.';
  expect(installState(view({install:{possible:false,reason:refused,acknowledgement:false}}))).toEqual({enabled:false,reasons:[refused]});
  expect(installState(view({install:{possible:false,reason:null,acknowledgement:false}})).reasons).toEqual(['The server cannot install this release now.']);
  // The console no longer judges the class, the signature or the refusals itself.
  expect(installState(view({available:{...release,signed:false,class:'manual'},refusals:['The checkout has local edits.']})).enabled).toBe(true);
  expect(installState(view({available:null})).enabled).toBe(false);
  expect(installState(view({install:null})).enabled).toBe(false);
  expect(installState(undefined).enabled).toBe(false);
  expect(logic).not.toContain('release.signed');
 });
 test('an apply or rollback under way keeps the page busy and is watched on load',()=>{
  expect(openRequest(view({request:{kind:'apply',state:'running',requestedAt:'r'}}))).toBe('apply');
  expect(openRequest(view({request:{kind:'rollback',state:'requested',requestedAt:'r'}}))).toBe('rollback');
  expect(openRequest(view({request:{kind:'check',state:'running',requestedAt:'r'}}))).toBeUndefined();
  expect(openRequest(view({request:{kind:'apply',state:'done',requestedAt:'r'}}))).toBeUndefined();
  expect(openRequest(view({last:{phase:'applied',from:'a',to:'b',startedAt:'s',automatic:false}}))).toBe('apply');
  expect(openRequest(view({last:{phase:'rolling_back',from:'a',to:'b',startedAt:'s',automatic:false}}))).toBe('rollback');
  expect(openRequest(view())).toBeUndefined();expect(openRequest(undefined)).toBeUndefined();
  expect(page).toContain('Boolean(openRequest(view))');
  expect(page).toContain('const kind=openRequest(view)');
 });
 test('a release whose verdict asks for the acknowledgement is offered behind it',()=>{
  expect(installState(view({available:{...release,class:'attended'},install:{possible:true,reason:null,acknowledgement:true}}))).toEqual({enabled:true,reasons:[]});
  expect(['safe','attended','rebuild','manual'].map(value=>installable(value as never))).toEqual([true,true,false,false]);
  expect(page).toContain('acknowledgement=Boolean(view.install?.acknowledgement)');
  expect(page).toContain('disabled={acknowledgement&&!acknowledged}');
  expect(page).toContain('{ACKNOWLEDGEMENT}');
  expect(page).toContain("run('apply',release.version,acknowledgement&&acknowledged)");
  expect(ACKNOWLEDGEMENT).toContain('backups taken before');
 });
 test('releases passed over are shown as information and never disable the install',()=>{
  const passed=view({skipped:['v0.3.0 was passed over: v0.3.0 is not signed'],
   newest:{version:'0.3.0',tag:'v0.3.0',class:'safe',signed:false,reasons:['v0.3.0 is not signed']}});
  expect(installState(passed)).toEqual({enabled:true,reasons:[]});
  expect(page).toContain('<Skipped notes={view.skipped}/>');
  expect(page).toContain('<Newest release={view.newest}');
  // With nothing to install, the newest release still shows instead of "No newer release".
  expect(page).toContain(':view.newest?null:<section className="details"><h2>No newer release</h2>');
 });
});

describe('responses while the server restarts',()=>{
 test('gateway errors, a briefly rejected token and 500 read as restarting, never as failure',()=>{
  for(const status of [401,500,502,503,504])expect(classifyStatus(status)).toBe('restarting');
  expect(classifyStatus(200)).toBe('ok');expect(classifyStatus(202)).toBe('ok');
  expect(classifyStatus(403)).toBe('forbidden');
  expect(classifyStatus(409)).toBe('refused');expect(classifyStatus(400)).toBe('refused');
  expect(classifyStatus(404)).toBe('failed');
 });
});

describe('progress state machine',()=>{
 const before=view({last:{phase:'confirmed',from:'0',to:'a',startedAt:'old',finishedAt:'old-end',automatic:false},request:{kind:'check',state:'done',requestedAt:'q0'}});
 const record=(phase:NonNullable<UpdatesView['last']>['phase'],automatic=false)=>({phase,from:'a',to:'b',version:'0.2.0',startedAt:'new',automatic,...(phase==='applied'?{}:{finishedAt:'end'})});
 function run(watch:Watch,events:[number,Parameters<typeof stepWatch>[1]][]){
  let current=watch,delay:number|null=0;
  for(const [minute,event] of events)({watch:current,delay}=stepWatch(current,event,at(minute)));
  return {watch:current,delay};
 }
 test('an upgrade goes queued, running, restarting, checking, then confirmed',()=>{
  const watch=startWatch('apply',before,at(0),false,'0.2.0');
  let step=stepWatch(watch,{type:'ok',view:{...before,request:{kind:'apply',version:'0.2.0',state:'requested',requestedAt:'q1'}}},at(0));
  expect(step.delay).toBe(2000);expect(watchStage(step.watch)).toBe('queued');
  step=stepWatch(step.watch,{type:'ok',view:{...before,request:{kind:'apply',state:'running',requestedAt:'q1'}}},at(1));
  expect(watchStage(step.watch)).toBe('running');
  step=stepWatch(step.watch,{type:'unreachable'},at(2));
  expect(step.watch.phase).toBe('restarting');expect(watchStage(step.watch)).toBe('restarting');expect(step.delay).toBe(4000);
  step=stepWatch(step.watch,{type:'unreachable'},at(2.1));expect(step.delay).toBe(8000);
  for(let tick=0;tick<5;tick++)step=stepWatch(step.watch,{type:'unreachable'},at(3));
  expect(step.delay).toBe(15000);
  step=stepWatch(step.watch,{type:'ok',view:{...before,last:record('applied'),request:{kind:'apply',state:'running',requestedAt:'q1'}}},at(4));
  expect(step.delay).toBe(2000);expect(step.watch.unreachable).toBe(0);expect(watchStage(step.watch)).toBe('checking');
  step=stepWatch(step.watch,{type:'ok',view:{...before,last:record('confirmed'),request:{kind:'apply',state:'done',requestedAt:'q1'}}},at(5));
  expect(step.delay).toBeNull();expect(step.watch.phase).toBe('done');expect(watchStage(step.watch)).toBe('confirmed');
  expect(stepWatch(step.watch,{type:'unreachable'},at(6)).delay).toBeNull();
 });
 test('an older finished record is never taken for the new outcome',()=>{
  const {watch,delay}=run(startWatch('apply',before,at(0)),[[0,{type:'ok',view:before}],[1,{type:'ok',view:before}]]);
  expect(delay).toBe(2000);expect(watch.phase).toBe('waiting');
 });
 test('the automatic way back and a failed way back are final states',()=>{
  const watch=startWatch('apply',before,at(0));
  const back=run(watch,[[1,{type:'unreachable'}],[3,{type:'ok',view:{...before,last:record('rolling_back',true)}}]]);
  expect(watchStage(back.watch)).toBe('returning');expect(back.delay).toBe(2000);
  const done=run(back.watch,[[4,{type:'ok',view:{...before,last:record('rolled_back',true)}}]]);
  expect(done.delay).toBeNull();expect(watchStage(done.watch)).toBe('rolled_back');
  const failed=run(watch,[[4,{type:'ok',view:{...before,last:{...record('rollback_failed',true),failure:'The previous version did not start either'}}}]]);
  expect(watchStage(failed.watch)).toBe('rollback_failed');
  const stopped=run(watch,[[1,{type:'ok',view:{...before,last:{...record('failed'),failure:'pull failed'}}}]]);
  expect(watchStage(stopped.watch)).toBe('failed');
 });
 test('a request the supervisor refuses ends the watch with its detail',()=>{
  const {watch,delay}=run(startWatch('apply',before,at(0)),[[0,{type:'ok',view:{...before,request:{kind:'apply',state:'failed',requestedAt:'q1',detail:'Pending receipts'}}}]]);
  expect(delay).toBeNull();expect(watchStage(watch)).toBe('refused');
 });
 test('silence past ten minutes times out, not fails; unreachable alone never fails early',()=>{
  const watch=startWatch('apply',before,at(0));
  const quiet=run(watch,Array.from({length:40},(_,index)=>[index*0.24,{type:'unreachable'}] as [number,{type:'unreachable'}]));
  expect(quiet.watch.phase).toBe('restarting');expect(quiet.delay).not.toBeNull();
  const late=run(quiet.watch,[[10,{type:'unreachable'}]]);
  expect(late.watch.phase).toBe('timed_out');expect(late.delay).toBeNull();expect(watchStage(late.watch)).toBe('timed_out');
  const answered=run(watch,[[11,{type:'ok',view:before}]]);
  expect(answered.watch.phase).toBe('timed_out');
 });
 test('a rollback watch ends on the way back, and an operator rollback reads as requested',()=>{
  const applied=view({last:record('applied'),canRollback:true});
  const watch=startWatch('rollback',applied,at(0));
  const pending=run(watch,[[0,{type:'ok',view:applied}]]);
  expect(watchStage(pending.watch)).toBe('queued');
  const done=run(watch,[[2,{type:'ok',view:{...applied,last:record('rolled_back',false)}}]]);
  expect(done.delay).toBeNull();expect(watchStage(done.watch)).toBe('rolled_back_by_request');
 });
 test('a check ends when the check time moves or its request finishes',()=>{
  const watch=startWatch('check',before,at(0));
  expect(run(watch,[[0,{type:'ok',view:before}]]).delay).toBe(2000);
  expect(watchStage(run(watch,[[0.2,{type:'ok',view:{...before,checkedAt:'2026-09-25T11:00:00Z'}}]]).watch)).toBe('checked');
  expect(run(watch,[[0.2,{type:'ok',view:{...before,request:{kind:'check',state:'done',requestedAt:'q1'}}}]]).delay).toBeNull();
  expect(run(watch,[[3,{type:'unreachable'}]]).watch.phase).toBe('timed_out');
 });
 test('a page loaded mid-upgrade resumes the watch and counts the request under way',()=>{
  const running=view({request:{kind:'apply',state:'running',requestedAt:'q1'}});
  expect(openRequest(running)).toBe('apply');
  expect(openRequest(view({last:record('applied')}))).toBe('apply');
  expect(openRequest(view({last:record('rolling_back')}))).toBe('rollback');
  const watch=startWatch('apply',running,at(0),true);
  expect(watchStage(run(watch,[[0,{type:'ok',view:running}]]).watch)).toBe('running');
  const refused=run(watch,[[0,{type:'ok',view:{...running,request:{kind:'apply',state:'failed',requestedAt:'q1'}}}]]);
  expect(watchStage(refused.watch)).toBe('refused');
  const checking=startWatch('apply',view({last:record('applied')}),at(0),true);
  expect(watchStage(run(checking,[[0,{type:'ok',view:view({last:record('applied')})}]]).watch)).toBe('checking');
 });
 test('every stage has words',()=>{for(const text of Object.values(STAGE_TEXT))expect(text.length).toBeGreaterThan(10);});
});

describe('wiring',()=>{
 test('the console shows updates to the operator only and reads the token on every call',()=>{
  expect(app).toContain('<UpdatesProvider client={updatesClient} enabled={operator}>');
  expect(app).toContain('updatesApi(()=>token.current)');
  expect(app).toContain("{operator&&<UpdateBanners");
  expect(app).toContain('<Updates/>');
  // Only the banner and the page read the update state, so a poll does not render the whole console.
  expect(app).not.toContain('useUpdates(');
  expect(page.match(/useUpdatesState\(\)/g)).toHaveLength(3);
  expect(app).toContain("setOperator(Boolean(organizations.data.operator))");
  expect(api).toContain("'/management/v1/updates'+path");
  for(const route of ["send('/check','POST')","send('/apply','POST',acknowledged?{version,acknowledged:true}:{version})","send('/rollback','POST')","send('/settings','PUT',settings)"])expect(api).toContain(route);
  // Every poll has its own deadline, so a proxy that never answers cannot stall the watch.
  expect(page).toContain('client.get(AbortSignal.timeout(10000))');
  expect(api).toContain('AbortSignal.timeout(10000)');
 });
 test('the confirmation states the backup, the pause and the automatic way back',()=>{
  expect(page).toContain('A backup of every environment is taken first.');
  expect(page).toContain('pause for a few minutes');
  expect(page).toContain('returns to this version by itself');
  expect(page).toContain("<button ref={install.trigger} className=\"primary\" disabled={!state.enabled}");
 });
 test('no long dashes anywhere in the update surfaces',()=>{
  for(const source of [app,page,logic,api])expect([...source].some(char=>char===String.fromCharCode(0x2013)||char===String.fromCharCode(0x2014))).toBe(false);
 });
});
