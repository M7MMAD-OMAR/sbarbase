import {useEffect,useMemo,useRef,useState} from 'react';
import {File,Folder,Pencil,Plus,Rocket,Trash2} from 'lucide-react';
import {ApiError,type Api} from '../api';
import {Editor} from './Editor';
import {Chip,Notice,Problem} from './kit';
import {treeRows} from './tree';
import {bytes} from './format';
import {buildDeploy,checkText,fileNameError,FUNCTION_NAME,rename} from './deploy';

const DEPTH=['d0','d1','d2','d3','d4','d5','d6'];
export type Deployed={name:string;boot_check?:string};

/** The files of one function, editable: a tree with Add File, rename and delete, the editor, and Deploy updates. It sends every file
 * to the deploy route, which starts the new version in the runtime before it goes live and refuses it if it cannot start. */
export function FileEditor({path,request,initial,verifyJwt,existingName,existingNames,onDeployed,onDirty}:{path:string;request:Api;initial:Record<string,string>;
 verifyJwt:boolean;existingName?:string;existingNames:string[];onDeployed:(result:Deployed)=>void;onDirty:(dirty:boolean)=>void}){
 const first=initial['index.ts']!==undefined?'index.ts':Object.keys(initial)[0]??'';
 const [files,setFiles]=useState<Record<string,string>>(initial),[baseline,setBaseline]=useState(initial),[selected,setSelected]=useState(first);
 const [name,setName]=useState(existingName??''),[adding,setAdding]=useState(false),[newFile,setNewFile]=useState(''),[renaming,setRenaming]=useState<string>(),[renameTo,setRenameTo]=useState('');
 const [confirm,setConfirm]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[done,setDone]=useState('');
 const [verify,setVerify]=useState(verifyJwt),status=useRef<HTMLParagraphElement>(null);
 const paths=Object.keys(files),dirty=JSON.stringify(files)!==JSON.stringify(baseline)||(!existingName&&name!=='');
 useEffect(()=>onDirty(dirty),[dirty]);
 useEffect(()=>{
  if(!dirty)return;
  const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();};
  window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);
 },[dirty]);
 useEffect(()=>()=>onDirty(false),[]);
 const rows=useMemo(()=>treeRows(paths),[paths.join('\n')]);
 const trimmed=name.trim();
 const nameProblem=!existingName&&trimmed!==''&&!FUNCTION_NAME.test(trimmed)?'Use letters, digits, - and _, starting with a letter or digit, at most 64.':'';
 const replaces=!existingName&&FUNCTION_NAME.test(trimmed)&&existingNames.includes(trimmed);
 const addProblem=newFile?fileNameError(newFile,paths):'',renameProblem=renaming&&renameTo?fileNameError(renameTo,paths.filter(item=>item!==renaming)):'';
 function setContent(value:string){setFiles(current=>({...current,[selected]:value}));setDone('');}
 function add(){
  const value=newFile.trim();if(fileNameError(value,paths))return;
  setFiles(current=>({...current,[value]:''}));setSelected(value);setAdding(false);setNewFile('');setDone('');setConfirm(false);setRenaming(undefined);
 }
 function finishRename(){
  const to=renameTo.trim();if(!renaming||fileNameError(to,paths.filter(item=>item!==renaming)))return;
  setFiles(current=>rename(current,renaming,to));if(selected===renaming)setSelected(to);setRenaming(undefined);setRenameTo('');setDone('');
 }
 function remove(){
  const left=paths.filter(item=>item!==selected);
  setFiles(current=>{const next={...current};delete next[selected];return next;});
  setSelected(left.includes('index.ts')?'index.ts':left[0]??'');setConfirm(false);setDone('');
 }
 async function deploy(){
  const target=existingName??trimmed;
  if(!FUNCTION_NAME.test(target)){setError('Give the function a name first.');return;}
  const built=buildDeploy(files,existingName?verifyJwt:verify);
  if(!built.ok){setError(built.reason);return;}
  setBusy(true);setError('');setDone('');
  try{
   const answer=await request(`${path}/functions/${target}`,'PUT',built.body);
   setBaseline(files);setDone(`Deployed ${target}. ${checkText(answer?.data?.deployed?.boot_check)}`);
   onDeployed({name:target,boot_check:answer?.data?.deployed?.boot_check});
  }catch(reason){
   // A version the runtime cannot start comes back as 422 with the runtime's own words; show them as they are.
   setError(reason instanceof ApiError&&reason.status===422&&reason.serverMessage?`The function did not start. ${reason.serverMessage}`:reason instanceof ApiError&&reason.serverMessage?reason.serverMessage:(reason as Error).message);
  }finally{setBusy(false);setTimeout(()=>status.current?.focus(),0);}
 }
 const current=files[selected];
 return <div className="fw-stack">
  {!existingName&&<div className="fw-field stack"><div className="fw-text"><label className="fw-label" htmlFor="new-function-name">Function name</label><p className="fw-hint">It becomes the address: <code>/functions/v1/{trimmed||'name'}</code>.</p></div>
   <input id="new-function-name" autoComplete="off" spellCheck={false} placeholder="hello-world" value={name} aria-invalid={Boolean(nameProblem)} onChange={event=>{setName(event.target.value);setDone('');}}/>
   {nameProblem&&<p className="fw-error" role="alert">{nameProblem}</p>}
   {replaces&&<Notice tone="wait">A function named <code>{trimmed}</code> exists. Deploying replaces its code at once, and its Verify JWT setting with the choice below.</Notice>}
   <label className="fw-check"><input type="checkbox" checked={verify} onChange={event=>setVerify(event.target.checked)}/>Verify JWT <Chip>turn off for webhooks</Chip></label></div>}
  <div className="fw-editor">
   <nav className="fw-files" aria-label="Files"><div className="fw-files-head"><h3>FILES</h3><button type="button" className="fw-ghost small" aria-expanded={adding} onClick={()=>{setAdding(!adding);setNewFile('');setConfirm(false);setRenaming(undefined);}}><Plus aria-hidden="true"/>Add File</button></div>
    {adding&&<form className="fw-addfile" onSubmit={event=>{event.preventDefault();add();}}><label className="fw-sr" htmlFor="new-file">File name</label>
     <input id="new-file" autoFocus autoComplete="off" spellCheck={false} placeholder="lib/helpers.ts" value={newFile} aria-invalid={Boolean(addProblem)} onChange={event=>setNewFile(event.target.value)}/>
     {addProblem&&<p className="fw-error" role="alert">{addProblem}</p>}<p className="fw-hint">Shared code: <code>../_shared/name.ts</code></p>
     <div className="fw-actions"><button type="submit" className="fw-primary" disabled={!newFile.trim()||Boolean(addProblem)}>Add</button><button type="button" className="fw-ghost" onClick={()=>setAdding(false)}>Cancel</button></div></form>}
    <ul>{rows.map(row=><li key={row.path}>{row.folder?<span className={'fw-folder '+DEPTH[Math.min(row.depth,6)]}><Folder aria-hidden="true"/><bdi>{row.name}</bdi></span>
     :<button type="button" className={(row.path===selected?'active ':'')+DEPTH[Math.min(row.depth,6)]} aria-current={row.path===selected?'true':undefined} onClick={()=>{setSelected(row.path);setConfirm(false);setRenaming(undefined);}}>
      <File aria-hidden="true"/><bdi>{row.name}</bdi>{files[row.path]!==baseline[row.path]&&<span className="fw-dot" role="img" aria-label="Changed"/>}</button>}</li>)}</ul></nav>
   <div className="fw-pane">
    {current===undefined?<Notice tone="info">This function has no files. Add one named index.ts.</Notice>:<>
     <div className="fw-pane-head">{renaming===selected?<form className="fw-inline-form" onSubmit={event=>{event.preventDefault();finishRename();}}>
       <label className="fw-sr" htmlFor="rename-file">New file name</label><input id="rename-file" autoFocus autoComplete="off" spellCheck={false} value={renameTo} aria-invalid={Boolean(renameProblem)} onChange={event=>setRenameTo(event.target.value)}/>
       <button type="submit" className="fw-primary" disabled={!renameTo.trim()||Boolean(renameProblem)||renameTo.trim()===selected}>Rename</button><button type="button" className="fw-ghost" onClick={()=>setRenaming(undefined)}>Cancel</button>
       {renameProblem&&<span className="fw-error" role="alert">{renameProblem}</span>}</form>
      :<><code>{selected}</code>{files[selected]!==baseline[selected]&&<Chip tone="wait">Changed</Chip>}<span className="fw-hint">{bytes(new Blob([current]).size)}</span>
       <span className="fw-grow"/>
       <button type="button" className="fw-icon" aria-label={`Rename ${selected}`} onClick={()=>{setRenaming(selected);setRenameTo(selected);setConfirm(false);}}><Pencil aria-hidden="true"/></button>
       {confirm?<span className="fw-inline-confirm"><span>Delete {selected}?</span><button type="button" className="fw-danger-solid" onClick={remove}>Delete</button><button type="button" className="fw-ghost" onClick={()=>setConfirm(false)}>Cancel</button></span>
        :<button type="button" className="fw-icon" aria-label={`Delete ${selected}`} onClick={()=>setConfirm(true)}><Trash2 aria-hidden="true"/></button>}</>}</div>
     <Editor value={current} path={selected} label={`Code of ${selected}`} onChange={setContent} readOnly={busy}/>
     <p className="fw-hint">Tab indents. Press Escape, then Tab, to leave the editor with the keyboard.</p></>}
   </div></div>
  <div className="fw-deploybar"><p ref={status} tabIndex={-1} className="fw-deploystatus" role="status">{busy?'Deploying and starting the new version…':done||(dirty?'You have changes that are not deployed.':'No changes.')}</p>
   <button type="button" className="fw-primary" disabled={busy||!dirty} onClick={()=>void deploy()}><Rocket aria-hidden="true"/>{existingName?'Deploy updates':replaces?'Replace function':'Deploy function'}</button></div>
  <Problem message={error}/>
 </div>;
}
