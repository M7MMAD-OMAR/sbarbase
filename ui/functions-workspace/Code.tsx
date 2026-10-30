import {useEffect,useMemo,useState} from 'react';
import {File,Folder} from 'lucide-react';
import type {Api} from '../api';
import {Loading} from '../components';
import {useLive} from './live';
import type {FunctionRow,SourceFile} from './types';
import {lines,languageOf} from './highlight';
import {treeRows} from './tree';
import {bytes} from './format';
import {Notice,Problem} from './kit';
import {FileEditor,type Deployed} from './FileEditor';

const DEPTH=['d0','d1','d2','d3','d4','d5','d6'];
const ids=new WeakMap<object,number>();let counter=0;
/** A number for each loaded answer, so the editor starts again on the source that arrived after a deploy, not on the one before. */
function idOf(value:object):number{let id=ids.get(value);if(!id){id=++counter;ids.set(value,id);}return id;}

function Viewer({file}:{file:SourceFile}){
 const rows=useMemo(()=>file.content===null?[]:lines(file.content,languageOf(file.path)),[file]);
 if(file.content===null)return <Notice tone="info">This file is {bytes(file.size)}, too large to show here. Download the function to read it.</Notice>;
 return <div className="fw-code" role="region" aria-label={`Code of ${file.path}`} tabIndex={0}><table><tbody>{rows.map((tokens,index)=><tr key={index}><th scope="row" className="no">{index+1}</th>
  <td>{tokens.length?tokens.map((token,position)=><span key={position} className={token.kind==='plain'?undefined:'tok-'+token.kind}>{token.text}</span>):'​'}</td></tr>)}</tbody></table></div>;
}

/** The files of the version that is running: a tree on one side and the code, read only, on the other. */
export function Code({fn,path,request,existingNames,onDeployed,onDirty}:{fn:FunctionRow;path:string;request:Api;existingNames:string[];onDeployed:(result:Deployed)=>void;onDirty:(dirty:boolean)=>void}){
 const data=useLive<{data:{files:SourceFile[];truncated:boolean}}>(signal=>request(`${path}/functions/${fn.name}/source`,'GET',undefined,signal),[path,fn.name,fn.updated_at]);
 const files=data.data?.data.files??[];
 const [selected,setSelected]=useState('');
 useEffect(()=>{if(files.length&&!files.some(file=>file.path===selected))setSelected((files.find(file=>file.path==='index.ts')??files[0]!).path);},[files,selected]);
 const rows=useMemo(()=>treeRows(files.map(file=>file.path)),[files]);
 const editable=data.data&&files.length>0&&files.every(file=>file.content!==null);
 const current=files.find(file=>file.path===selected);
 return <div className="fw-stack"><Problem message={data.error} retry={data.refresh}/>
  {data.loading&&!data.data?<Loading/>:editable?<FileEditor key={idOf(data.data!)} path={path} request={request} existingName={fn.name} existingNames={existingNames} verifyJwt={fn.verify_jwt} onDeployed={onDeployed} onDirty={onDirty}
   initial={Object.fromEntries(files.map(file=>[file.path,file.content as string]))}/>:<div className="fw-editor">
   <nav className="fw-files" aria-label="Files"><h3>FILES</h3><ul>{rows.map(row=><li key={row.path}>{row.folder?<span className={'fw-folder '+DEPTH[Math.min(row.depth,6)]}><Folder aria-hidden="true"/><bdi>{row.name}</bdi></span>
    :<button type="button" className={(row.path===selected?'active ':'')+DEPTH[Math.min(row.depth,6)]} aria-current={row.path===selected?'true':undefined} onClick={()=>setSelected(row.path)}><File aria-hidden="true"/><bdi>{row.name}</bdi></button>}</li>)}</ul></nav>
   <div className="fw-pane">{current&&<><div className="fw-pane-head"><code>{current.path}</code><span className="fw-hint">{bytes(current.size)} · read only</span></div><Viewer file={current}/></>}
    {data.data?.data.truncated&&<p className="fw-hint">Some files are too large to show. Download the function to read them all.</p>}
    <Notice tone="info">Some files are too large to edit here, so this is a read only view. Download the function, or deploy again from your project folder.</Notice></div></div>}
 </div>;
}
