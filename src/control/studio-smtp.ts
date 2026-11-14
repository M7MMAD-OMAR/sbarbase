import {Catalog} from './catalog';
import {reply} from './auth';
import {smtpHandler,type SmtpView} from './smtp';
import {KINDS,mailTemplatesHandler,type TemplateView} from './mail-templates';
import {PRIVATE_DIRECTORY} from './settings-route';
import {SAVE_INTERVAL_MS} from './sign-in';
import {withManagementAuthorization} from './management-context';

const MASK='********';
const fields={SMTP_ADMIN_EMAIL:'admin_email',SMTP_SENDER_NAME:'sender_name',SMTP_HOST:'host',SMTP_PORT:'port',SMTP_USER:'user',SMTP_PASS:'password'} as const;
const limits={SMTP_MAX_FREQUENCY:'min_interval_seconds',RATE_LIMIT_EMAIL_SENT:'rate_limit_email_sent'} as const;
const permitted=new Set([...Object.keys(fields),...Object.keys(limits)]);
// The pinned Studio's Emails > Templates screens read and write these keys. Bodies and subjects map onto the environment's
// private templates file (src/control/mail-templates.ts), the same one the Sbarbase console's Templates panel edits.
const subjectKey=(kind:string)=>'MAILER_SUBJECTS_'+kind.toUpperCase(),contentKey=(kind:string)=>'MAILER_TEMPLATES_'+kind.toUpperCase()+'_CONTENT';
const templateKeys=new Map<string,{kind:string;member:'subject'|'body'}>(KINDS.flatMap(kind=>[[subjectKey(kind),{kind,member:'subject'}],[contentKey(kind),{kind,member:'body'}]]));
// Security notification emails are not offered by the pinned Auth configuration Sbarbase writes: they read as off and cannot be turned on.
const NOTIFICATIONS=['PASSWORD_CHANGED','EMAIL_CHANGED','PHONE_CHANGED','IDENTITY_LINKED','IDENTITY_UNLINKED','MFA_FACTOR_ENROLLED','MFA_FACTOR_UNENROLLED'];
const notificationKeys=new Set(NOTIFICATIONS.map(name=>'MAILER_NOTIFICATIONS_'+name+'_ENABLED'));
const templateFields=(templates:TemplateView[])=>{
 const fields:Record<string,unknown>={},subjects:Record<string,boolean>={},contents:Record<string,boolean>={};
 for(const entry of templates){
  fields[subjectKey(entry.kind)]=entry.subject;fields[contentKey(entry.kind)]=entry.body;
  subjects[subjectKey(entry.kind)]=entry.subject!==entry.default_subject;contents[contentKey(entry.kind)]=entry.body!==entry.default_body;
 }
 for(const key of notificationKeys)fields[key]=false;
 return {...fields,MAILER_SUBJECTS_CUSTOM_CONTENTS:subjects,MAILER_TEMPLATES_CUSTOM_CONTENTS:contents};
};
const view=(smtp:SmtpView)=>({
 SMTP_ADMIN_EMAIL:smtp.enabled?smtp.admin_email:null,SMTP_SENDER_NAME:smtp.enabled?smtp.sender_name:null,
 SMTP_HOST:smtp.enabled?smtp.host:null,SMTP_PORT:smtp.enabled?String(smtp.port):null,
 SMTP_USER:smtp.enabled?smtp.user:null,SMTP_PASS:smtp.password_set?MASK:null,
 SMTP_MAX_FREQUENCY:smtp.min_interval_seconds,RATE_LIMIT_EMAIL_SENT:smtp.rate_limit_email_sent,
 MAILER_AUTOCONFIRM:!smtp.confirm_email,
});

/** The pinned native Emails screens' contract: SMTP settings and the six Auth email templates. The Studio session supplies the runtime;
 * neither a native project ref nor a request body can select a different environment. */
export function studioSmtpConfig(catalog:Catalog,signInDirectory=PRIVATE_DIRECTORY,mailDirectory=PRIVATE_DIRECTORY,saveInterval=SAVE_INTERVAL_MS){
 return async(actor:string,runtime:string,request:Request,authorized:()=>boolean=()=>true):Promise<Response>=>{
  const pathname=new URL(request.url).pathname,reset=pathname.match(/^\/api\/platform\/auth\/default\/templates\/([a-z-]{1,40})\/reset$/);
  const spam=pathname==='/api/platform/auth/default/validate/spam';
  if(pathname!=='/api/platform/auth/default/config'&&!reset&&!spam)return reply(404,{message:'Unknown auth configuration route'});
  if(!(reset||spam?['POST']:['GET','PATCH']).includes(request.method))return reply(405,{message:'Method not allowed'});
  try{
   const environment=catalog.studioNavigation(actor,runtime).current.environment.id;
   const nativeAuthorized=()=>authorized()&&!request.signal.aborted;
   const current=()=>nativeAuthorized()&&catalog.studioAllowed(actor,runtime)&&catalog.signIn(actor,environment).runtime===runtime;
   if(!current())return reply(403,{message:'Forbidden'});
   // No spam scoring service runs here, so the check reports no rules and the native save goes on.
   if(spam)return reply(200,{rules:[]});
   const identify=async()=>current()?actor:null;
   const handler=smtpHandler(catalog,identify,signInDirectory,mailDirectory,saveInterval);
   const templates=mailTemplatesHandler(catalog,identify,signInDirectory,mailDirectory,saveInterval);
   // Keep the original Studio session alive through the reused handlers' own awaits.
   // Catalog guards inside those handlers recheck membership independently of this pure scope.
   const invoke=(request:Request)=>withManagementAuthorization(nativeAuthorized,()=>handler(request));
   const invokeTemplates=(request:Request)=>withManagementAuthorization(nativeAuthorized,()=>templates(request));
   const path='http://management.internal/management/v1/environments/'+environment+'/smtp';
   const templatesPath='http://management.internal/management/v1/environments/'+environment+'/mail-templates';
   const get=await invoke(new Request(path));
   if(!get.ok)return get;
   const snapshot=await get.json() as {data:{smtp:SmtpView;revision:number}};
   const getTemplates=await invokeTemplates(new Request(templatesPath));
   // A templates file the server cannot read stops template edits, never the SMTP screen.
   const stored=getTemplates.ok?await getTemplates.json() as {data:{templates:TemplateView[];revision:number}}:null;
   if(!current())return reply(403,{message:'Forbidden'});
   const full=(smtp:SmtpView,list:TemplateView[]|undefined)=>({...view(smtp),...(list?templateFields(list):{})});
   const saveTemplates=async(changes:Record<string,Record<string,string|null>>)=>{
    if(!stored)return getTemplates;
    const put=await invokeTemplates(new Request(templatesPath,{method:'PUT',headers:{'content-type':'application/json'},
     body:JSON.stringify({base_revision:stored.data.revision,templates:changes})}));
    if(!put.ok)return put;
    const result=await put.json() as {data:{templates:TemplateView[];state:string}};
    if(!current())return reply(403,{message:'Forbidden'});
    const response=reply(reset?200:202,full(snapshot.data.smtp,result.data.templates));
    response.headers.set('x-sbarbase-settings-state',result.data.state);
    return response;
   };
   if(reset){
    const kind=reset[1]!.replace(/-/g,'_');
    if(!KINDS.includes(kind))return reply(400,{message:'Security notification emails are not available in Sbarbase'});
    return await saveTemplates({[kind]:{subject:null,body:null}});
   }
   if(request.method==='GET')return reply(200,full(snapshot.data.smtp,stored?.data.templates));
   if(request.headers.get('content-type')?.split(';')[0]?.trim()!=='application/json')return reply(415,{message:'JSON is required'});
   let input:unknown;try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
   if(!current())return reply(403,{message:'Forbidden'});
   if(!input||typeof input!=='object'||Array.isArray(input))return reply(400,{message:'Invalid SMTP settings'});
   const given=input as Record<string,unknown>;
   const keys=Object.keys(given);
   if(keys.some(key=>notificationKeys.has(key)&&given[key]!==false))return reply(400,{message:'Security notification emails are not available in Sbarbase'});
   const templateChanges=keys.filter(key=>templateKeys.has(key));
   if(templateChanges.length){
    if(keys.some(key=>!templateKeys.has(key)&&!notificationKeys.has(key)))return reply(400,{message:'Save templates and SMTP settings separately'});
    const changes:Record<string,Record<string,string|null>>={};
    for(const key of templateChanges){
     const value=given[key],{kind,member}=templateKeys.get(key)!;
     if(value!==null&&typeof value!=='string')return reply(400,{message:'Invalid template'});
     (changes[kind]??={})[member]=value as string|null;
    }
    return await saveTemplates(changes);
   }
   // Turning every notification off changes nothing: they are always off here.
   if(keys.length&&keys.every(key=>notificationKeys.has(key)))return reply(200,full(snapshot.data.smtp,stored?.data.templates));
   const body=given;
   if(!Object.keys(body).length||Object.keys(body).some(key=>!permitted.has(key)))return reply(400,{message:'Unsupported SMTP setting'});
   const disabling=Object.keys(fields).every(key=>body[key]===null);
   if(!disabling&&Object.keys(fields).some(key=>body[key]===null))return reply(400,{message:'SMTP fields may be cleared only when disabling custom SMTP'});
   const smtp=snapshot.data.smtp;
   const enabled=!disabling&&(smtp.enabled||Object.keys(fields).some(key=>body[key]!==undefined));
   const translated:Record<string,unknown>={enabled,base_revision:snapshot.data.revision};
   if(enabled){
    for(const [native,field] of Object.entries(fields)){
     const value=body[native]??(field==='password'?'':smtp[field as keyof SmtpView]);
     if(field==='port'){
      if(typeof value!=='string'&&typeof value!=='number')return reply(400,{message:'Invalid SMTP port'});
      if(!/^\d+$/.test(String(value)))return reply(400,{message:'Invalid SMTP port'});
      translated.port=Number(value);
     }else translated[field]=value;
    }
    if(translated.password===MASK)return reply(400,{message:'Leave the password empty to keep the stored password'});
   }
   for(const [native,field] of Object.entries(limits))if(body[native]!==undefined)translated[field]=body[native];
   const saved=await invoke(new Request(path,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify(translated)}));
   if(!saved.ok)return saved;
   const result=await saved.json() as {data:{smtp:SmtpView;state:string}};
   if(!current())return reply(403,{message:'Forbidden'});
   const response=reply(saved.status,full(result.data.smtp,stored?.data.templates));
   response.headers.set('x-sbarbase-settings-state',result.data.state);
   return response;
  }catch(error){
   return reply(error instanceof Error&&error.message==='Forbidden'?403:409,{message:error instanceof Error&&error.message==='Forbidden'?'Forbidden':'SMTP settings are unavailable'});
  }
 };
}
