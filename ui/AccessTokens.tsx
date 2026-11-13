import {useEffect,useRef,useState,type FormEvent} from 'react';
import {Bot,Copy,Plus,ShieldAlert} from 'lucide-react';
import {useData,type Api,type Organization,type Project} from './api';
import {ErrorMessage,Empty} from './components';
import {AGENT_PERMISSIONS,READ_PERMISSIONS,SAFE_ADMIN_PERMISSIONS,DANGEROUS_PERMISSIONS,type AgentPermission} from '../src/control/agent-permissions';

type Scope={kind:'all'}|{kind:'organization';id:string}|{kind:'project';id:string};
type Token={id:string;name:string;scope:Scope;permissions?:AgentPermission[];read_only:boolean;created_at:number;expires_at:number;last_used_at:number|null;revoked_at:number|null};
type Preset='read'|'admin'|'custom'|'all';
const labels:Record<AgentPermission,string>={
 'projects.read':'عرض المشاريع والبيئات','projects.write':'إنشاء المشاريع والبيئات وتعديل أسمائها',
 'organization.read':'عرض المنظمات','organization.write':'تعديل اسم المنظمة',
 'auth.read':'عرض إعدادات المصادقة','auth.write':'تعديل أمان تسجيل الدخول وروابط إعادة التوجيه والبريد','auth.delete':'حذف موفري تسجيل الدخول الموحد',
 'database.read':'قراءة قاعدة البيانات','database.configure':'تعديل واجهة البيانات والمهام المجدولة','database.write':'تنفيذ SQL والترحيلات وتمكين الاتصال المباشر',
 'functions.read':'عرض الدوال وكودها وأسماء أسرارها','functions.write':'نشر كود الدوال وإدارة أسرارها','functions.delete':'حذف الدوال',
 'storage.read':'عرض معلومات التخزين المدعومة',
 'keys.read':'عرض مفاتيح API العامة وبيانات المفاتيح','keys.write':'إنشاء مفاتيح API، بما فيها المفاتيح السرية','keys.delete':'إلغاء مفاتيح API',
 'logs.read':'عرض السجلات والمقاييس','realtime.read':'عرض إعدادات Realtime','realtime.write':'تعديل إعدادات Realtime',
};
const date=(value:number,time=false)=>new Date(value).toLocaleString('ar-EG-u-nu-latn',time?{dateStyle:'short',timeStyle:'short',hour12:true}:{dateStyle:'medium'});

/** One-time token display and owner-controlled capabilities share the backend's permission catalog. */
export function AccessTokens({request,organizations}:{request:Api;organizations:Organization[]}){
 const tokens=useData<{data:Token[]}>(signal=>request('/access-tokens','GET',undefined,signal),[request]);
 const [name,setName]=useState(''),[scopeKind,setScopeKind]=useState<Scope['kind']>('project'),[organization,setOrganization]=useState(organizations[0]?.id??'');
 const [project,setProject]=useState(''),[days,setDays]=useState('30'),[preset,setPreset]=useState<Preset>('read');
 const [permissions,setPermissions]=useState<AgentPermission[]>([...READ_PERMISSIONS]),[acknowledged,setAcknowledged]=useState(false);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[created,setCreated]=useState<{token:string;name:string;scope:Scope;permissions:AgentPermission[]}>(),[confirm,setConfirm]=useState<string>(),[copied,setCopied]=useState('');
 const [client,setClient]=useState<'json'|'codex'|'claude'>('json');
 const saving=useRef(false),createdHeading=useRef<HTMLHeadingElement>(null);
 useEffect(()=>{if(created)createdHeading.current?.focus();},[created]);
 const projects=useData<{data:Project[]}|undefined>(signal=>scopeKind==='project'&&organization?request(`/organizations/${organization}/projects`,'GET',undefined,signal):Promise.resolve(undefined),[request,scopeKind,organization]);
 const origin=location.origin;
 const dangerous=permissions.filter(permission=>DANGEROUS_PERMISSIONS.includes(permission));
 const missing=!name.trim()?'اسم الرمز مطلوب.':scopeKind!=='all'&&!organization?'اختيار المنظمة مطلوب.':scopeKind==='project'&&!project?'اختيار المشروع مطلوب.':!permissions.length?'اختيار صلاحية واحدة على الأقل مطلوب.':dangerous.length&&!acknowledged?'تأكيد فهم مخاطر الصلاحيات المحدّدة مطلوب.':'';
 function choosePreset(value:Preset){setPreset(value);setAcknowledged(false);if(value!=='custom')setPermissions([...(value==='read'?READ_PERMISSIONS:value==='admin'?SAFE_ADMIN_PERMISSIONS:AGENT_PERMISSIONS)]);}
 function toggle(permission:AgentPermission){setPreset('custom');setAcknowledged(false);setPermissions(current=>current.includes(permission)?current.filter(item=>item!==permission):[...current,permission]);}
 async function copy(text:string){try{await navigator.clipboard.writeText(text);setCopied('نُسخ إلى الحافظة.');}catch{setCopied('تعذر النسخ. يمكن تحديد النص ونسخه يدويًا.');}}
 async function create(event:FormEvent){
  event.preventDefault();if(saving.current||missing)return;saving.current=true;setBusy(true);setError('');setCopied('');
  const scope:Scope=scopeKind==='all'?{kind:'all'}:{kind:scopeKind,id:scopeKind==='organization'?organization:project};
  try{const result=await request('/access-tokens','POST',{name:name.trim(),scope,permissions,read_only:permissions.every(permission=>permission.endsWith('.read')),days:Number(days)});setCreated({token:result.token,name:name.trim(),scope,permissions:[...permissions]});setName('');setAcknowledged(false);tokens.refresh();}
  catch(e){setError((e as Error).message);}finally{saving.current=false;setBusy(false);}
 }
 async function revoke(id:string){if(saving.current)return;saving.current=true;setBusy(true);setError('');try{await request('/access-tokens/'+id,'DELETE');setConfirm(undefined);tokens.refresh();}catch(e){setError((e as Error).message);}finally{saving.current=false;setBusy(false);}}
 const scopeLabel=(scope:Scope)=>scope.kind==='all'?'كل ما يسمح به دورك':scope.kind==='organization'?`منظمة ${organizations.find(item=>item.id===scope.id)?.name??scope.id.slice(0,8)}`:`مشروع ${projects.data?.data?.find(item=>item.id===scope.id)?.name??scope.id.slice(0,8)}`;
 const configuration=created?(client==='json'?JSON.stringify({mcpServers:{sbarbase:{url:origin+'/mcp',headers:{Authorization:'Bearer '+created.token}}}},null,2):client==='codex'?`# Set in the terminal that starts Codex:\nexport SBARBASE_ACCESS_TOKEN='${created.token}'\n\n# Add to ~/.codex/config.toml:\n[mcp_servers.sbarbase]\nurl = "${origin}/mcp"\nbearer_token_env_var = "SBARBASE_ACCESS_TOKEN"`:`claude mcp add --transport http sbarbase ${origin}/mcp --header "Authorization: Bearer ${created.token}"`):'';
 const live=(tokens.data?.data??[]).filter(token=>!token.revoked_at&&token.expires_at>Date.now());
 const ended=(tokens.data?.data??[]).filter(token=>token.revoked_at||token.expires_at<=Date.now());
 return <div className="agent-access" dir="rtl" lang="ar">
  <div className="page-heading"><div><h1>صلاحيات الوكلاء</h1><p className="muted">رمز مستقل لكلّ وكيل، مع تحديد المشاريع التي يستطيع الوصول إليها والعمليات التي يستطيع تنفيذها.</p></div></div>
  <div id="agent-token-errors"><ErrorMessage message={error||tokens.error||projects.error}/></div>
  {created&&<section className="details new-key" aria-labelledby="agent-created-heading"><h2 id="agent-created-heading" ref={createdHeading} tabIndex={-1}><Bot aria-hidden="true"/>ربط الوكيل</h2>
   <p>صدر الرمز <bdi>{created.name}</bdi> للوصول إلى {scopeLabel(created.scope)}، وعدد صلاحياته {created.permissions.length}.</p>
   <label htmlFor="new-token">الرمز، يظهر مرة واحدة فقط</label><textarea id="new-token" dir="ltr" readOnly value={created.token} spellCheck={false}/>
   <div className="form-row"><button onClick={()=>void copy(created.token)}><Copy aria-hidden="true"/>نسخ الرمز</button></div>
   <label htmlFor="agent-endpoint">عنوان MCP</label><input id="agent-endpoint" dir="ltr" readOnly value={origin+'/mcp'}/>
   <label htmlFor="agent-client">إعداد الربط</label><select id="agent-client" value={client} onChange={event=>setClient(event.target.value as typeof client)}><option value="json">Cursor والعملاء الذين يقبلون JSON</option><option value="codex">Codex</option><option value="claude">Claude Code</option></select>
   <label htmlFor="agent-configuration">الإعداد الخاص بالعميل</label><textarea id="agent-configuration" dir="ltr" readOnly rows={client==='json'?11:client==='codex'?8:3} value={configuration} spellCheck={false}/>
   <p className="small muted">الإعداد المعروض يتضمّن الرمز. يجب حفظه محليًا خارج المستودعات والرسائل المشتركة. الربط هنا بالرمز، ولا يتضمّن تسجيل الدخول عبر OAuth.</p>
   <div className="form-row"><button onClick={()=>void copy(configuration)}><Copy aria-hidden="true"/>نسخ إعداد الربط</button><button onClick={()=>{setCreated(undefined);setCopied('');}}>إخفاء الرمز بعد حفظه</button></div>
   <p className="small">بعد الربط، يستطيع الوكيل طلب <code dir="ltr">get_sbarbase_operations</code> لمعرفة العمليات المسموحة، ثمّ <code dir="ltr">execute_sbarbase_operation</code> لمعاينتها وتنفيذها.</p>
  </section>}
  <p className="small muted" role="status">{copied}</p>
  <section className="details"><h2>رمز جديد</h2>
   <p className="small callout"><ShieldAlert aria-hidden="true"/>صلاحيات الرمز مقيّدة بدورك الحالي. لا تسمح رموز الوكلاء بإصدار رموز أخرى أو بتعديل أمان حسابك أو بحذف المشاريع والبيئات نهائيًا.</p>
   <form onSubmit={create} aria-busy={busy} aria-describedby="agent-token-errors agent-submit-help">
    <fieldset disabled={busy} className="agent-token-fields">
     <label htmlFor="token-name">اسم الرمز</label><input id="token-name" value={name} maxLength={64} placeholder="وكيل المشروع على جهازي" onChange={event=>setName(event.target.value)} required autoComplete="off"/>
     <div className="agent-form-grid"><div><label htmlFor="token-scope">نطاق الوصول</label><select id="token-scope" value={scopeKind} onChange={event=>{setScopeKind(event.target.value as Scope['kind']);setAcknowledged(false);}}><option value="project">مشروع واحد</option><option value="organization">منظمة واحدة</option><option value="all">كل ما يسمح به دورك</option></select></div>
      <div><label htmlFor="token-days">مدة الصلاحية</label><select id="token-days" value={days} onChange={event=>setDays(event.target.value)}>{['7','30','90','180','365'].map(value=><option key={value} value={value}>{value} {value==='7'?'أيام':'يومًا'}</option>)}</select></div></div>
     {scopeKind!=='all'&&<><label htmlFor="token-organization">المنظمة</label><select id="token-organization" value={organization} onChange={event=>{setOrganization(event.target.value);setProject('');setAcknowledged(false);}} required><option value="">اختيار المنظمة</option>{organizations.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></>}
     {scopeKind==='project'&&<><label htmlFor="token-project">المشروع</label>{projects.loading?<p role="status">جارٍ تحميل المشاريع...</p>:<select id="token-project" value={project} onChange={event=>{setProject(event.target.value);setAcknowledged(false);}} required><option value="">اختيار المشروع</option>{(projects.data?.data??[]).map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select>}</>}
     <p className="small muted">نطاق المنظمة يشمل مشاريعها الحالية والمستقبلية. نطاق الوصول الكامل يشمل كل ما يمنحه لك دورك الآن ولاحقًا.</p>
     <label htmlFor="token-preset">مجموعة الصلاحيات</label><select id="token-preset" value={preset} onChange={event=>choosePreset(event.target.value as Preset)}><option value="read">قراءة فقط</option><option value="admin">إدارة الإعدادات</option><option value="custom">صلاحيات محدّدة</option><option value="all">كل الصلاحيات المتاحة للوكيل</option></select>
     <p className="small muted">مجموعة إدارة الإعدادات تستبعد تنفيذ SQL ونشر الكود وإصدار المفاتيح وصلاحيات الحذف والإلغاء. وقد تؤثر تغييرات الإعدادات في أمان التطبيق أو توافره.</p>
     <fieldset className="agent-permissions" aria-describedby="agent-permission-help"><legend>الصلاحيات المحدّدة: {permissions.length}</legend><p id="agent-permission-help" className="small muted">كلّ صلاحية مستقلة. يمكن تعديل الاختيار مباشرة.</p>
      <div className="agent-permission-grid">{AGENT_PERMISSIONS.filter(permission=>!DANGEROUS_PERMISSIONS.includes(permission)).map(permission=><label className="check" key={permission}><input type="checkbox" checked={permissions.includes(permission)} onChange={()=>toggle(permission)}/><span>{labels[permission]}<code dir="ltr">{permission}</code></span></label>)}</div>
     </fieldset>
     <fieldset className="agent-permissions agent-sensitive" aria-describedby="agent-danger-help"><legend>صلاحيات تحتاج عناية خاصة</legend><p id="agent-danger-help" className="small">تنفيذ SQL ونشر الكود وإصدار المفاتيح السرية قد يتيح للوكيل تغيير البيانات أو حذفها. هذه الصلاحيات مخصّصة لوكيل تثق به.</p>
      <div className="agent-permission-grid">{DANGEROUS_PERMISSIONS.map(permission=><label className="check" key={permission}><input type="checkbox" checked={permissions.includes(permission)} onChange={()=>toggle(permission)}/><span>{labels[permission]}<code dir="ltr">{permission}</code></span></label>)}</div>
      {dangerous.length>0&&<label className="check agent-risk-ack"><input type="checkbox" checked={acknowledged} onChange={event=>setAcknowledged(event.target.checked)} aria-describedby="agent-danger-help" required/><span>أفهم أنّ الصلاحيات المحدّدة قد تسمح بتغييرات مدمّرة للبيانات، وأوافق على منحها لهذا الرمز.</span></label>}
     </fieldset>
    </fieldset>
    <p id="agent-submit-help" className="small muted" role="status">{missing||'الرمز جاهز للإصدار بالصلاحيات المحدّدة.'}</p>
    <div className="form-row"><button type="submit" className="primary" disabled={busy||!!missing}><Plus aria-hidden="true"/>{busy?'جارٍ إصدار الرمز...':'إصدار الرمز'}</button></div>
   </form>
  </section>
  <section className="details"><h2>الرموز الحالية</h2>{tokens.loading?<p role="status">جارٍ تحميل الرموز...</p>:live.length?<div className="table-wrap"><table><thead><tr><th scope="col">الاسم</th><th scope="col">النطاق</th><th scope="col">الصلاحيات</th><th scope="col">انتهاء الصلاحية</th><th scope="col">آخر استخدام</th><th scope="col">الإجراء</th></tr></thead><tbody>
   {live.map(token=><tr key={token.id}><td><bdi>{token.name}</bdi></td><td>{scopeLabel(token.scope)}</td><td><details><summary>عدد الصلاحيات: {(token.permissions??READ_PERMISSIONS).length}</summary><ul className="agent-token-grants">{(token.permissions??READ_PERMISSIONS).map(permission=><li key={permission}>{labels[permission]??permission}</li>)}</ul></details></td><td>{date(token.expires_at)}</td><td>{token.last_used_at?date(token.last_used_at,true):'لم يُستخدم'}</td>
    <td>{confirm===token.id?<div className="confirm"><span>إلغاء الرمز يمنع الطلبات الجديدة. قد تكتمل عملية بدأت بالفعل.</span><button className="danger" disabled={busy} onClick={()=>void revoke(token.id)}>تأكيد إلغاء الرمز</button><button disabled={busy} onClick={()=>setConfirm(undefined)}>تراجع</button></div>:<button disabled={busy} onClick={()=>setConfirm(token.id)} aria-label={'إلغاء الرمز '+token.name}>إلغاء الرمز</button>}</td></tr>)}</tbody></table></div>:!tokens.error&&<Empty>لا توجد رموز سارية.</Empty>}
   {ended.length>0&&<p className="small muted">عدد الرموز الملغاة أو المنتهية خلال آخر 30 يومًا: {ended.length}.</p>}
   <p className="small muted">إلغاء الرمز أو انتهاء صلاحيته أو تعيين كلمة مرور جديدة للحساب من الخادم يوقف الطلبات الجديدة. تتغير صلاحياته عند تغيير عضويتك أو دورك. عند الحاجة لتعديل صلاحيات رمز، يُلغى ويصدر رمز جديد.</p>
  </section>
 </div>;
}
