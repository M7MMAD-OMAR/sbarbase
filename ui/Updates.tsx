import {useCallback,useEffect,useId,useRef,useState,type KeyboardEvent,type ReactNode} from 'react';
import {CircleArrowUp,CircleCheck,Download,ExternalLink,Info,OctagonAlert,RefreshCw,RotateCcw,ShieldAlert,ShieldCheck,TriangleAlert,Undo2,Wrench,X} from 'lucide-react';
import {UpdateError,type UpdatesApi} from './api';
import {ErrorMessage,Loading} from './components';
import {ACKNOWLEDGEMENT,BACKUP_GUIDE,CLASS_WORDS,STAGE_TEXT,UPGRADES_GUIDE,availableText,banners,newestText,busy,describeWindow,dismiss,formatWhen,settingsKey,installState,installable,parseClock,readDismissed,
 releaseNotes,resumeKind,startWatch,stepWatch,toClock24,watchStage,windowError,type ClockTime,type NewestRelease,type PollEvent,type ServerZone,type UpdateClass,type UpdateSettings,type UpdatesView,type Watch,type WatchKind} from './releases';

function storage(){try{return window.localStorage;}catch{return undefined;}}
const message=(error:unknown)=>error instanceof Error?error.message:'The request failed. Refresh and try again.';

export type UpdatesController={
 view?:UpdatesView;error:string;loading:boolean;
 progress?:{watch:Watch;delay:number|null};
 dismissed:string[];
 refresh:()=>void;
 begin:(kind:WatchKind,version?:string,acknowledged?:boolean)=>Promise<void>;
 close:()=>void;
 dismiss:(key:string)=>void;
 saveSettings:(settings:UpdateSettings)=>Promise<void>;
};

/** Update state for the installation operator. It lives in the console shell, not in a page,
 * so a watched upgrade survives navigation and token refreshes during the restart. A 403
 * leaves everything empty: someone who is not the operator sees nothing about updates. */
export function useUpdates(client:UpdatesApi,enabled:boolean):UpdatesController{
 const [view,setView]=useState<UpdatesView>(),[error,setError]=useState(''),[loading,setLoading]=useState(false);
 const [progress,setProgress]=useState<{watch:Watch;delay:number|null}>(),[dismissed,setDismissed]=useState<string[]>(()=>readDismissed(storage()));
 const watching=useRef(false);watching.current=Boolean(progress&&progress.delay!==null);
 const refresh=useCallback(()=>{
  if(!enabled)return;setLoading(true);
  client.get().then(value=>{setView(value);setError('');})
   .catch(reason=>{if(reason instanceof UpdateError&&reason.outcome==='forbidden'){setView(undefined);setError('');}else setError(message(reason));})
   .finally(()=>setLoading(false));
 },[client,enabled]);
 useEffect(()=>{if(!enabled){setView(undefined);return;}refresh();
  const timer=setInterval(()=>{if(!watching.current)refresh();},5*60*1000);return()=>clearInterval(timer);},[enabled,refresh]);
 // A page loaded while an upgrade is under way picks up the watch where it stands.
 const resumed=useRef(false);
 useEffect(()=>{if(resumed.current||!view)return;resumed.current=true;const kind=resumeKind(view);
  if(kind&&!progress)setProgress({watch:startWatch(kind,view,Date.now(),true),delay:2000});},[view]);
 useEffect(()=>{
  if(!progress||progress.delay===null)return;let live=true;const current=progress.watch;
  const timer=setTimeout(async()=>{
   let event:PollEvent;
   try{event={type:'ok',view:await client.get(AbortSignal.timeout(10000))};}catch{event={type:'unreachable'};}
   if(!live)return;if(event.type==='ok')setView(event.view);setProgress(stepWatch(current,event,Date.now()));
  },progress.delay);
  return()=>{live=false;clearTimeout(timer);};
 },[progress,client]);
 async function begin(kind:WatchKind,version?:string,acknowledged?:boolean){
  // A fresh read is the baseline, so an older request is never mistaken for this one.
  const before=await client.get().catch(()=>view);
  if(kind==='apply'&&version)await client.apply(version,acknowledged);else if(kind==='rollback')await client.rollback();else await client.check();
  setProgress({watch:startWatch(kind,before,Date.now(),false,version),delay:500});
 }
 return {view,error,loading,progress,dismissed,refresh,begin,
  close:()=>{setProgress(undefined);refresh();},
  dismiss:key=>setDismissed(dismiss(storage(),key)),
  saveSettings:async settings=>{const saved=await client.saveSettings(settings);setView(current=>current&&{...current,settings:saved});}};
}

const CLASS_ICON:Record<UpdateClass,typeof ShieldCheck>={safe:ShieldCheck,attended:ShieldAlert,rebuild:Wrench,manual:TriangleAlert};
const CLASS_STATE:Record<UpdateClass,string>={safe:'applied',attended:'running',rebuild:'running',manual:'failed'};
function ClassBadge({value}:{value:UpdateClass}){
 const Icon=CLASS_ICON[value];
 return <span className={'update-class state '+CLASS_STATE[value]}><Icon aria-hidden="true"/>{CLASS_WORDS[value].label}</span>;
}
const inProgress=(progress:UpdatesController['progress'])=>Boolean(progress&&progress.watch.kind!=='check'&&progress.delay!==null);

/** App-wide notices for the operator: a newer release, the outcome of the last upgrade, or
 * an upgrade under way. Each notice states itself in words, not by colour alone. */
export function UpdateBanners({updates,onOpen,onPage}:{updates:UpdatesController;onOpen:()=>void;onPage:boolean}){
 const open=onPage?null:<button className="secondary" onClick={onOpen}>View updates</button>;
 if(inProgress(updates.progress))return <div className="update-banner" role="status"><RefreshCw aria-hidden="true"/><p>An update is in progress. The console may disconnect for a few minutes while Sbarbase restarts.</p>{open}</div>;
 const watch=updates.progress?.watch;
 // Wherever the operator waited, a confirmed upgrade asks for a reload so the new console loads.
 if(watch&&watch.kind==='apply'&&updates.progress?.delay===null&&watchStage(watch)==='confirmed')
  return <div className="update-banner done" role="status"><CircleCheck aria-hidden="true"/><p>Sbarbase was updated{watch.view?.last?.version?' to '+watch.view.last.version:''}. Reload the console so it loads the new version.</p><button className="primary" onClick={()=>location.reload()}><RotateCcw aria-hidden="true"/>Reload console</button></div>;
 const shown=banners(updates.view,updates.dismissed);
 if(!shown.length)return null;
 return <>{shown.map(banner=>{
  const close=banner.dismissible?<button className="banner-close" aria-label="Dismiss this notice" title="Dismiss" onClick={()=>updates.dismiss(banner.key)}><X aria-hidden="true"/></button>:null;
  if(banner.kind==='rollback_failed')return <div key={banner.key} className="update-banner critical" role="alert"><OctagonAlert aria-hidden="true"/><p><strong>The update failed and so did the way back.</strong> Restore from the backups taken before the upgrade. <a href={BACKUP_GUIDE} target="_blank" rel="noreferrer">How to restore<ExternalLink aria-hidden="true"/></a></p>{open}</div>;
  if(banner.kind==='rolled_back')return <div key={banner.key} className="update-banner" role="status"><Undo2 aria-hidden="true"/><p>{STAGE_TEXT.rolled_back}</p>{open}{close}</div>;
  if(banner.kind==='confirmed')return <div key={banner.key} className="update-banner done" role="status"><CircleCheck aria-hidden="true"/><p>Sbarbase was updated to {banner.version}.</p>{close}</div>;
  if(banner.kind==='newest')return <div key={banner.key} className="update-banner" role="status"><Info aria-hidden="true"/><p>{newestText(banner)}</p>{open}{close}</div>;
  return <div key={banner.key} className="update-banner" role="status"><CircleArrowUp aria-hidden="true"/><p>{availableText(banner)}</p>{open}{close}</div>;
 })}</>;
}

/** A confirmation step inside the page, like the console's other confirmations, that takes
 * focus when it opens, closes on Escape and hands focus back to the button that opened it. */
function Confirm({title,children,action,danger,busy,disabled,onConfirm,onCancel}:{title:string;children:ReactNode;action:string;danger?:boolean;busy:boolean;disabled?:boolean;onConfirm:()=>void;onCancel:()=>void}){
 const id=useId(),heading=useRef<HTMLHeadingElement>(null);
 useEffect(()=>{heading.current?.focus();},[]);
 const key=(event:KeyboardEvent)=>{if(event.key==='Escape'&&!busy){event.stopPropagation();onCancel();}};
 return <div className="confirm-panel" role="group" aria-labelledby={id} onKeyDown={key}><h3 id={id} ref={heading} tabIndex={-1}>{title}</h3>{children}
  <div className="form-row"><button className={danger?'danger':'primary'} disabled={busy||disabled} onClick={onConfirm}>{busy?'Sending…':action}</button><button onClick={onCancel} disabled={busy}>Cancel</button></div></div>;
}

function useFocusReturn(){
 const trigger=useRef<HTMLButtonElement>(null);
 return {trigger,restore:()=>requestAnimationFrame(()=>trigger.current?.focus())};
}

function Progress({updates}:{updates:UpdatesController}){
 const progress=updates.progress,heading=useRef<HTMLHeadingElement>(null);
 useEffect(()=>{heading.current?.focus();},[]);
 if(!progress)return null;
 const {watch}=progress,stage=watchStage(watch),last=watch.view?.last,request=watch.view?.request;
 const running=progress.delay!==null,finalState=stage==='confirmed'||stage==='rolled_back_by_request'?'applied':stage==='rolled_back'?'running':running?'running':'failed';
 return <section className="details update-progress" aria-labelledby="update-progress"><h2 id="update-progress" ref={heading} tabIndex={-1}>{watch.kind==='rollback'?'Roll back':'Update to '+(watch.version??'the new version')}</h2>
  <p role="status" aria-live="polite"><span className={'state '+finalState}>{running?'In progress':'Finished'}</span> {STAGE_TEXT[stage]}</p>
  {stage==='confirmed'&&<><p>Reload the console so it loads the new version.</p><button className="primary" onClick={()=>location.reload()}><RotateCcw aria-hidden="true"/>Reload console</button></>}
  {stage==='rollback_failed'&&<p className="error"><a href={BACKUP_GUIDE} target="_blank" rel="noreferrer">How to restore from a backup<ExternalLink aria-hidden="true"/></a></p>}
  {(stage==='failed'||stage==='rollback_failed')&&last?.failure&&<p className="notice">{last.failure}</p>}
  {stage==='refused'&&request?.detail&&<p className="notice">{request.detail}</p>}
  {!running&&stage!=='confirmed'&&<div className="actions"><button onClick={updates.close}>Close</button></div>}
 </section>;
}

function ClockPicker({label,value,onChange}:{label:string;value:string;onChange:(value:string)=>void}){
 const id=useId(),time:ClockTime=parseClock(value)??{hour:12,minute:0,period:'AM'};
 const minutes=Array.from({length:12},(_,index)=>index*5);if(!minutes.includes(time.minute))minutes.push(time.minute),minutes.sort((a,b)=>a-b);
 const set=(change:Partial<ClockTime>)=>onChange(toClock24({...time,...change}));
 return <fieldset className="clock"><legend>{label}</legend><div className="form-row">
  <label className="sr-only" htmlFor={id+'-hour'}>{label} hour</label><select id={id+'-hour'} value={time.hour} onChange={event=>set({hour:Number(event.target.value)})}>{Array.from({length:12},(_,index)=>index+1).map(hour=><option key={hour} value={hour}>{hour}</option>)}</select>
  <span aria-hidden="true">:</span>
  <label className="sr-only" htmlFor={id+'-minute'}>{label} minute</label><select id={id+'-minute'} value={time.minute} onChange={event=>set({minute:Number(event.target.value)})}>{minutes.map(minute=><option key={minute} value={minute}>{String(minute).padStart(2,'0')}</option>)}</select>
  <label className="sr-only" htmlFor={id+'-period'}>{label}, AM or PM</label><select id={id+'-period'} value={time.period} onChange={event=>set({period:event.target.value as ClockTime['period']})}><option value="AM">AM</option><option value="PM">PM</option></select>
 </div></fieldset>;
}

function Settings({updates,settings,timezone}:{updates:UpdatesController;settings:UpdateSettings;timezone:ServerZone}){
 const [form,setForm]=useState(settings),[busy,setBusy]=useState(false),[error,setError]=useState(''),[saved,setSaved]=useState('');
 // Reset only when the saved values change: each poll hands over a new, equal object.
 useEffect(()=>setForm(settings),[settingsKey(settings)]);
 const invalid=form.automatic?windowError(form.window):'';
 const changed=JSON.stringify(form)!==JSON.stringify(settings);
 async function save(){setBusy(true);setError('');setSaved('');try{await updates.saveSettings(form);setSaved('Settings saved.');}catch(e){setError(message(e));}finally{setBusy(false);}}
 return <section className="details"><h2>Settings</h2>
  <label className="check"><input type="checkbox" checked={form.check} onChange={event=>setForm({...form,check:event.target.checked,automatic:event.target.checked&&form.automatic})}/>Check for new releases</label>
  <p className="small muted">Sbarbase looks for a newer signed release every 6 hours and shows it here. Checking never installs anything.</p>
  <label className="check"><input type="checkbox" checked={form.automatic} disabled={!form.check} onChange={event=>setForm({...form,automatic:event.target.checked})}/>Install safe updates automatically</label>
  <p className="small muted">{form.check?'Only safe, signed releases install by themselves, and only inside the maintenance window. A release that changes environment databases always waits for you, and one that rolled back is never tried again automatically.':'Turn on checking first: automatic updates need it.'}</p>
  {form.automatic&&<div className="window"><h3>Maintenance window</h3><div className="window-row">
   <ClockPicker label="Starts" value={form.window.start} onChange={start=>setForm({...form,window:{...form.window,start}})}/>
   <ClockPicker label="Ends" value={form.window.end} onChange={end=>setForm({...form,window:{...form.window,end}})}/></div>
   <p className="small muted">{invalid||describeWindow(form.window,timezone)}</p></div>}
  <ErrorMessage message={error}/>
  <div className="actions form-row"><button className="primary" disabled={busy||!changed||Boolean(invalid)} onClick={()=>void save()}>{busy?'Saving…':'Save settings'}</button><span className="small muted" role="status">{saved}</span></div>
 </section>;
}

function Commands({lines}:{lines:string[]}){return <pre className="log commands"><code>{lines.join('\n')}</code></pre>;}

/** Notes about releases the check passed over: information, never a reason the release on
 * offer cannot be installed. */
function Skipped({notes}:{notes:string[]}){
 if(!notes.length)return null;
 return <><h3>Releases passed over</h3><ul className="plain-list small muted">{notes.map(note=><li key={note}><Info aria-hidden="true"/>{note}</li>)}</ul></>;
}

/** The newest release when it is not the one on offer: that it exists, and why it is not offered. */
function Newest({release,offered,notes}:{release:NewestRelease;offered:boolean;notes:string[]}){
 return <section className="details" aria-labelledby="newest-heading">
  <div className="section-heading"><h2 id="newest-heading">{offered?'Also released: ':'Newer release: '}Sbarbase {release.version}</h2>{release.class&&<ClassBadge value={release.class}/>}</div>
  <p className="small muted">Tag <code>{release.tag}</code>. {release.signed?'Signed by a Sbarbase release key.':'Not signed by a Sbarbase release key.'}</p>
  <p>{offered?'This installation cannot install it, so the newest release it can install is offered instead.':'This installation cannot install it.'}</p>
  {release.reasons.length>0&&<><h3>Why</h3><ul className="plain-list">{release.reasons.map(reason=><li key={reason}>{reason}</li>)}</ul></>}
  {release.class==='manual'&&<p>It needs a manual migration on the server. <a href={UPGRADES_GUIDE} target="_blank" rel="noreferrer">Read the upgrades guide<ExternalLink aria-hidden="true"/></a></p>}
  {!offered&&notes.length>1&&<Skipped notes={notes}/>}
 </section>;
}

/** The installation's updates page: what runs now, what is available, how to install it,
 * the last upgrade and the update settings. Only the installation operator reaches it. */
export function Updates({updates}:{updates:UpdatesController}){
 const view=updates.view;
 const [confirm,setConfirm]=useState<'install'|'rollback'>(),[sending,setSending]=useState(false),[error,setError]=useState(''),[checking,setChecking]=useState(false);
 const [acknowledged,setAcknowledged]=useState(false),acknowledgeId=useId();
 const install=useFocusReturn(),back=useFocusReturn(),reasonsId=useId();
 async function run(kind:WatchKind,version?:string,acknowledgement?:boolean){
  setSending(true);setError('');
  try{await updates.begin(kind,version,acknowledgement);setConfirm(undefined);setAcknowledged(false);}catch(e){setError(message(e));}finally{setSending(false);}
 }
 async function check(){setChecking(true);setError('');try{await updates.begin('check');}catch(e){setError(message(e));}finally{setChecking(false);}}
 const heading=<div className="page-heading"><div><h1>Updates</h1><p className="muted">Sbarbase versions for this installation. Only the installation operator sees this page.</p></div></div>;
 if(!view)return <>{heading}<ErrorMessage message={updates.error}/>{updates.loading?<Loading/>:updates.error&&<button className="secondary" onClick={updates.refresh}><RefreshCw aria-hidden="true"/>Refresh</button>}</>;
 const release=view.available,state=installState(view),watch=updates.progress?.watch;
 const checkRunning=Boolean(checking||watch?.kind==='check'&&updates.progress?.delay!==null);
 const checkDone=watch?.kind==='check'&&updates.progress?.delay===null?(watchStage(watch)==='timed_out'?'The check has not finished yet. Look again later.':release?'Checked. A newer release is available.':'Checked. This is the newest release.'):'';
 const notes=release?releaseNotes(release.notes,document.documentElement.lang||'en'):undefined;
 const moving=inProgress(updates.progress)||busy(view);
 return <>{heading}
  {watch&&watch.kind!=='check'&&<Progress key={watch.since} updates={updates}/>}
  <ErrorMessage message={error}/>
  <section className="details"><h2>Installed version</h2>
   <p><strong>Sbarbase {view.current.version}</strong> <code>{view.current.commit.slice(0,12)}</code></p>
   <p className="small muted">{view.settings.check?'Last checked: '+formatWhen(view.checkedAt):'Checking for new releases is off.'}</p>
   {view.checkError&&<p className="notice">The last check did not finish: {view.checkError}</p>}
   <div className="form-row"><button disabled={checkRunning||moving} onClick={()=>void check()}><RefreshCw aria-hidden="true"/>{checkRunning?'Checking…':'Check now'}</button><span className="small muted" role="status">{checkDone}</span></div>
  </section>
  {release?<section className="details" aria-labelledby="release-heading">
   <div className="section-heading"><h2 id="release-heading">Sbarbase {release.version}</h2><ClassBadge value={release.class}/></div>
   <p className="small muted">Tag <code>{release.tag}</code>, commit <code>{release.commit.slice(0,12)}</code>. {release.signed?'Signed by a Sbarbase release key.':'Not signed.'}</p>
   <p>{CLASS_WORDS[release.class].explanation}</p>
   {notes&&notes.text.trim()&&<><h3>What is new</h3><div className="release-notes" lang={notes.language} dir="auto">{notes.text}</div></>}
   {release.reasons.length>0&&<><h3>Why it is classed this way</h3><ul className="plain-list">{release.reasons.map(reason=><li key={reason}>{reason}</li>)}</ul></>}
   {release.changes.length>0&&<><h3>What changes</h3><div className="table-wrap compact"><table><thead><tr><th>Component</th><th>Now</th><th>After the update</th></tr></thead>
    <tbody>{release.changes.map(change=><tr key={change.label}><td>{change.label}</td><td><code>{change.before}</code></td><td><code>{change.after}</code></td></tr>)}</tbody></table></div></>}
   {view.refusals.length>0&&<><h3>Why it cannot be installed now</h3><ul className="plain-list refusals">{view.refusals.map(refusal=><li key={refusal}><TriangleAlert aria-hidden="true"/>{refusal}</li>)}</ul></>}
   <Skipped notes={view.skipped}/>
   {installable(release.class)?<div className="actions">{confirm==='install'
    ?<Confirm title={'Install Sbarbase '+release.version+'?'} action="Install now" busy={sending} disabled={release.class==='attended'&&!acknowledged}
      onConfirm={()=>void run('apply',release.version,release.class==='attended'&&acknowledged)} onCancel={()=>{setConfirm(undefined);setAcknowledged(false);install.restore();}}>
      <ul className="plain-list"><li>A backup of every environment is taken first.</li><li>The console and your applications pause for a few minutes while Sbarbase restarts.</li><li>If the new version does not start healthy, Sbarbase returns to this version by itself.</li>
       {release.class==='attended'&&<li><strong>This update changes your environment databases as it starts.</strong> The way back restores the console's own state only: if Sbarbase returns to this version, environment data may need restoring from the backups taken before the update. <a href={BACKUP_GUIDE} target="_blank" rel="noreferrer">How to restore<ExternalLink aria-hidden="true"/></a></li>}</ul>
      {release.class==='attended'&&<label className="check" htmlFor={acknowledgeId}><input id={acknowledgeId} type="checkbox" checked={acknowledged} onChange={event=>setAcknowledged(event.target.checked)}/>{ACKNOWLEDGEMENT}</label>}</Confirm>
    :<><button ref={install.trigger} className="primary" disabled={!state.enabled} aria-describedby={state.enabled?undefined:reasonsId} onClick={()=>{setAcknowledged(false);setConfirm('install');}}><Download aria-hidden="true"/>Install update</button>
      {!state.enabled&&<p id={reasonsId} className="small muted">{state.reasons.join(' ')}</p>}</>}</div>
   :release.class==='rebuild'?<div className="actions"><h3>Install it on the server</h3>
     <p>Move the checkout to the release, then rebuild. With Docker:</p>
     <Commands lines={['docker compose exec sbarbase python3 lab/upgrade.py start --release '+release.tag+' --allow-class rebuild','docker compose up -d --build']}/>
     <p>With the systemd service:</p>
     <Commands lines={['/usr/bin/python3 lab/upgrade.py start --release '+release.tag+' --allow-class rebuild','sudo /usr/bin/python3 lab/install_server.py supervise --apply','sudo systemctl restart sbarbase']}/>
     <p className="small muted">Give <code>supervise --apply</code> the same options the service was installed with. It installs the new unit; the restart then starts Sbarbase on the release.</p>
     <p><a href={UPGRADES_GUIDE} target="_blank" rel="noreferrer">Read the upgrades guide<ExternalLink aria-hidden="true"/></a></p></div>
   :<div className="actions"><h3>This release needs a manual migration</h3><p>Follow the upgrades guide on the server before installing it. The console does not install it.</p>
     <p><a href={UPGRADES_GUIDE} target="_blank" rel="noreferrer">Read the upgrades guide<ExternalLink aria-hidden="true"/></a></p></div>}
  </section>:view.newest?null:<section className="details"><h2>No newer release</h2><p className="muted">{view.settings.check?'This installation runs the newest release it knows of.':'Turn on checking below, or press Check now, to look for a newer release.'}</p></section>}
  {view.newest&&view.newest.version!==release?.version&&<Newest release={view.newest} offered={Boolean(release)} notes={view.skipped}/>}
  {view.last&&<section className="details"><h2>Last update</h2>
   <p><span className={'state '+(view.last.phase==='confirmed'?'applied':view.last.phase==='rollback_failed'||view.last.phase==='failed'?'failed':'running')}>{PHASE_WORDS[view.last.phase]}</span></p>
   <p className="small muted">From <code>{view.last.from.slice(0,12)}</code> to {view.last.version?'Sbarbase '+view.last.version+' ':''}<code>{view.last.to.slice(0,12)}</code>. Started {formatWhen(view.last.startedAt)}{view.last.finishedAt?', finished '+formatWhen(view.last.finishedAt):''}.{view.last.phase==='rolled_back'&&view.last.automatic?' The way back was automatic.':''}</p>
   {view.last.failure&&<p className="notice">{view.last.failure}</p>}
   {view.last.phase==='rollback_failed'&&<p><a href={BACKUP_GUIDE} target="_blank" rel="noreferrer">How to restore from a backup<ExternalLink aria-hidden="true"/></a></p>}
   {view.canRollback&&(confirm==='rollback'
    ?<Confirm title="Roll back to the previous version?" action="Roll back" danger busy={sending} onConfirm={()=>void run('rollback')} onCancel={()=>{setConfirm(undefined);back.restore();}}>
      <ul className="plain-list"><li>Sbarbase returns to the version it ran before the last update.</li><li>The console and your applications pause for a few minutes while it restarts.</li><li>The backups taken before the update stay where they are.</li></ul></Confirm>
    :<div className="actions"><button ref={back.trigger} className="danger" disabled={moving} onClick={()=>setConfirm('rollback')}><Undo2 aria-hidden="true"/>Roll back</button></div>)}
  </section>}
  <Settings updates={updates} settings={view.settings} timezone={view.timezone}/>
 </>;
}

const PHASE_WORDS:Record<NonNullable<UpdatesView['last']>['phase'],string>={
 applied:'Started, checking health',confirmed:'Installed',rolling_back:'Returning to the previous version',
 rolled_back:'Back on the previous version',rollback_failed:'The way back failed',failed:'Did not go ahead'};

