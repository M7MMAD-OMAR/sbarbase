import {useState} from 'react';
import type {Api} from './api';
import {SmtpPanel} from './SmtpPanel';
import {TemplatesPanel} from './TemplatesPanel';

/** The cloud's Emails page: SMTP Settings and Templates, each saving on its own. */
export function EmailsTab({path,request,onSaved}:{path:string;request:Api;onSaved:()=>void}){
 const [page,setPage]=useState<'smtp'|'templates'>('smtp');
 return <>
  <nav className="aw-sub" aria-label="Email settings">
   <button type="button" className={page==='smtp'?'active':''} aria-current={page==='smtp'?'page':undefined} onClick={()=>setPage('smtp')}>SMTP Settings</button>
   <button type="button" className={page==='templates'?'active':''} aria-current={page==='templates'?'page':undefined} onClick={()=>setPage('templates')}>Templates</button></nav>
  {/* Both stay mounted, so an unsaved edit survives a look at the other page. */}
  <div hidden={page!=='smtp'}><SmtpPanel path={path} request={request} onSaved={onSaved}/></div>
  <div hidden={page!=='templates'}><TemplatesPanel path={path} request={request} onSaved={onSaved}/></div>
 </>;
}
