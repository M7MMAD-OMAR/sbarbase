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

const DEPTH=['d0','d1','d2','d3','d4','d5','d6'];

function Viewer({file}:{file:SourceFile}){
 const rows=useMemo(()=>file.content===null?[]:lines(file.content,languageOf(file.path)),[file]);
 if(file.content===null)return <Notice tone="info">This file is {bytes(file.size)}, too large to show here. Download the function to read it.</Notice>;
 return <div className="fw-code" role="region" aria-label={`Code of ${file.path}`} tabIndex={0}><table><tbody>{rows.map((tokens,index)=><tr key={index}><th scope="row" className="no">{index+1}</th>
  <td>{tokens.length?tokens.map((token,position)=><span key={position} className={token.kind==='plain'?undefined:'tok-'+token.kind}>{token.text}</span>):'​'}</td></tr>)}</tbody></table></div>;
}

/** The files of the version that is running: a tree on one side and the code, read only, on the other. */
export function Code({fn,path,request}:{fn:FunctionRow;path:string;request:Api}){
 const data=useLive<{data:{files:SourceFile[];truncated:boolean}}>(signal=>request(`${path}/functions/${fn.name}/source`,'GET',undefined,signal),[path,fn.name,fn.updated_at]);
 const files=data.data?.data.files??[];
 const [selected,setSelected]=useState('');
 useEffect(()=>{if(files.length&&!files.some(file=>file.path===selected))setSelected((files.find(file=>file.path==='index.ts')??files[0]!).path);},[files,selected]);
 const rows=useMemo(()=>treeRows(files.map(file=>file.path)),[files]);
 const current=files.find(file=>file.path===selected);
 return <div className="fw-stack"><Problem message={data.error} retry={data.refresh}/>
  {data.loading&&!data.data?<Loading/>:<div className="fw-editor">
   <nav className="fw-files" aria-label="Files"><h3>FILES</h3><ul>{rows.map(row=><li key={row.path}>{row.folder?<span className={'fw-folder '+DEPTH[Math.min(row.depth,6)]}><Folder aria-hidden="true"/>{row.name}</span>
    :<button type="button" className={(row.path===selected?'active ':'')+DEPTH[Math.min(row.depth,6)]} aria-current={row.path===selected?'true':undefined} onClick={()=>setSelected(row.path)}><File aria-hidden="true"/>{row.name}</button>}</li>)}</ul></nav>
   <div className="fw-pane">{current&&<><div className="fw-pane-head"><code>{current.path}</code><span className="fw-hint">{bytes(current.size)} · read only</span></div><Viewer file={current}/></>}
    {data.data?.data.truncated&&<p className="fw-hint">Some files are too large to show. Download the function to read them all.</p>}
    <p className="fw-hint">This is the version that is running. To change it, deploy again from your project folder or from Add function.</p></div></div>}
 </div>;
}
