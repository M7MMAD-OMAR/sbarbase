import {useEffect,useRef,useState,type FormEvent} from 'react';
import {Folder,ChevronLeft,Plus,Search,RotateCcw,RefreshCw} from 'lucide-react';
import {useData,type Api,type Organization,type Project} from './api';
import {ErrorMessage} from './components';

type ArchivedProject=Project&{archived_at:number;retained_environments:number};
const date=(value:number)=>new Date(value).toLocaleDateString('ar-EG-u-nu-latn',{dateStyle:'medium'});
export function Projects({organization,request,onSelect}:{organization:Organization;request:Api;onSelect:(project:Project,environment?:string,tab?:Window|null)=>void}){
 const result=useData<{data:Project[]}>(signal=>request(`/organizations/${organization.id}/projects`,'GET',undefined,signal),[organization.id,request]);
 const archived=useData<{data:ArchivedProject[]}>(signal=>organization.role==='viewer'?Promise.resolve({data:[]}):request(`/organizations/${organization.id}/archived-projects`,'GET',undefined,signal),[organization.id,organization.role,request]);
 const [query,setQuery]=useState(''),[creating,setCreating]=useState(false),[name,setName]=useState(''),[busy,setBusy]=useState(false),[restoring,setRestoring]=useState<string>(),[restored,setRestored]=useState<string>(),[error,setError]=useState(''),[message,setMessage]=useState('');
 const saving=useRef(false),restoredCard=useRef<HTMLButtonElement>(null),nameInput=useRef<HTMLInputElement>(null);
 useEffect(()=>{if(creating)nameInput.current?.focus();},[creating]);
 useEffect(()=>{if(restored)restoredCard.current?.focus();},[restored,result.data]);
 const matches=(item:Project)=>item.name.toLowerCase().includes(query.trim().toLowerCase());
 const projects=result.data?.data.filter(matches)??[],retained=archived.data?.data.filter(matches)??[];
 async function create(event:FormEvent){
  event.preventDefault();if(saving.current||!name.trim())return;saving.current=true;setBusy(true);setError('');
  const title=name.trim(),tab=window.open('about:blank','_blank');
  if(tab){tab.opener=null;tab.document.title='جارٍ تجهيز المشروع';tab.document.body.textContent='جارٍ تجهيز بيئة production. ستفتح لوحة Supabase عند اكتمال التجهيز. يمكن متابعة الحالة من لوحة الإدارة.';}
  try{const created=await request(`/organizations/${organization.id}/projects`,'POST',{name:title}) as {id:string;environment:string};setCreating(false);setName('');onSelect({id:created.id,name:title},created.environment,tab);}
  catch(error){tab?.close();const refused=error as {status?:number;serverMessage?:string};setError(refused.status===409&&refused.serverMessage==='Name already used'?'الاسم مستخدم في هذه المنظمة. يمكن استعادة المشروع المؤرشف من الأرشيف.':'تعذّر إنشاء المشروع. تحقّق من الاسم والاتصال ثمّ أعد المحاولة.');}
  finally{saving.current=false;setBusy(false);}
 }
 async function restore(project:ArchivedProject){
  if(saving.current)return;saving.current=true;setRestoring(project.id);setError('');setMessage('');
  try{await request(`/projects/${project.id}/restore`,'POST');setRestored(project.id);setMessage('عاد المشروع إلى القائمة. بقيت بيئاته المحذوفة ضمن إجراءات الاستبقاء.');result.refresh();archived.refresh();}
  catch{setError('تعذّرت استعادة المشروع. تحقّق من اتصالك وصلاحياتك ثمّ أعد المحاولة.');}
  finally{saving.current=false;setRestoring(undefined);}
 }
 return <div dir="rtl" lang="ar">
  <div className="page-heading"><div><h1>المشاريع</h1><p className="muted">اختيار مشروع لإدارة بيئاته وفتح لوحة Supabase.</p></div>{organization.role!=='viewer'&&<button className="primary" onClick={()=>{setCreating(true);setError('');}} disabled={creating||!!restoring}><Plus aria-hidden="true"/>مشروع جديد</button>}</div>
  {creating&&<form className="create-form" onSubmit={create} aria-busy={busy} aria-describedby="project-action-error"><p className="notice">تُنشأ بيئة production تلقائيًّا، وتفتح لوحة Supabase في تبويب جديد عند اكتمال التجهيز.</p><label htmlFor="new-project-name">اسم المشروع</label><div className="form-row"><input id="new-project-name" ref={nameInput} value={name} maxLength={100} required disabled={busy} onChange={event=>setName(event.target.value)}/><button type="submit" className="primary" disabled={busy||!name.trim()}>{busy?'جارٍ الإنشاء...':'إنشاء المشروع'}</button><button type="button" disabled={busy} onClick={()=>setCreating(false)}>إلغاء</button></div></form>}
  <div className="search"><Search aria-hidden="true"/><input aria-label="بحث في المشاريع" placeholder="بحث في المشاريع" value={query} onChange={event=>setQuery(event.target.value)}/></div>
  <div id="project-action-error"><ErrorMessage message={error}/></div><ErrorMessage message={result.error?'تعذّر تحميل المشاريع. يمكن تحديث القائمة للمحاولة مجدّدًا.':''}/>
  {result.error&&<button onClick={result.refresh}><RefreshCw aria-hidden="true"/>تحديث المشاريع</button>}
  <p className="small muted" role="status">{message}</p>
  {result.loading?<p role="status">جارٍ تحميل المشاريع...</p>:projects.length?<><ul className="project-grid" aria-label="المشاريع النشطة">{projects.map(project=><li key={project.id}><button ref={project.id===restored?restoredCard:undefined} className="project-card" aria-label={'فتح مشروع '+project.name} onClick={()=>onSelect(project)}><span className="project-card-icon"><Folder aria-hidden="true"/></span><span className="project-card-title"><bdi>{project.name}</bdi></span><span className="project-card-id">معرّف المشروع <code dir="ltr" title={project.id}>{project.id.slice(0,8)}</code></span><span className="project-card-footer">فتح المشروع<ChevronLeft aria-hidden="true"/></span></button></li>)}</ul><p className="page-summary small muted">عدد المشاريع النشطة: {projects.length}.</p></>:!result.error&&<div className="empty">{query?'لا توجد مشاريع نشطة تطابق البحث.':organization.role==='viewer'?'لا توجد مشاريع نشطة. يمكن للمالك أو أحد مسؤولي المنظمة إنشاء مشروع.':'لا توجد مشاريع نشطة. يمكن البدء بإنشاء مشروع جديد.'}</div>}
  {organization.role!=='viewer'&&<section className="details" aria-labelledby="archived-projects-heading" aria-busy={archived.loading||!!restoring}><h2 id="archived-projects-heading">أرشيف المشاريع</h2><p className="small muted">تحتفظ هذه المشاريع بسجلّات بيئاتها المحذوفة. استعادة المشروع تعيده إلى القائمة، ولا تشغّل بيئاته تلقائيًّا.</p>
   <ErrorMessage message={archived.error?'تعذّر تحميل الأرشيف. يمكن تحديث القائمة للمحاولة مجدّدًا.':''}/>
   {archived.error&&<button onClick={archived.refresh}><RefreshCw aria-hidden="true"/>تحديث الأرشيف</button>}
   {archived.loading?<p role="status">جارٍ تحميل الأرشيف...</p>:retained.length?<ul className="project-grid" aria-label="المشاريع المؤرشفة">{retained.map(project=><li key={project.id} className="project-card"><span className="project-card-title"><bdi>{project.name}</bdi></span><p className="small muted">تاريخ الأرشفة: {date(project.archived_at)}<br/>عدد البيئات المحتفظ بسجلّاتها: {project.retained_environments}.</p>{organization.role==='owner'?<button disabled={!!restoring||busy} aria-label={'استعادة مشروع '+project.name} onClick={()=>void restore(project)}><RotateCcw aria-hidden="true"/>{restoring===project.id?'جارٍ الاستعادة...':'استعادة المشروع'}</button>:<p className="small muted">استعادة المشروع متاحة لمالك المنظمة.</p>}</li>)}</ul>:!archived.error&&<p className="small muted">{query?'لا توجد مشاريع مؤرشفة تطابق البحث.':'لا توجد مشاريع في الأرشيف.'}</p>}
  </section>}
 </div>;
}
