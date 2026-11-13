import {Catalog} from './catalog';
import {reply} from './auth';
import {smtpHandler,type SmtpView} from './smtp';
import {PRIVATE_DIRECTORY} from './settings-route';
import {SAVE_INTERVAL_MS} from './sign-in';
import {withManagementAuthorization} from './management-context';

const MASK='********';
const fields={SMTP_ADMIN_EMAIL:'admin_email',SMTP_SENDER_NAME:'sender_name',SMTP_HOST:'host',SMTP_PORT:'port',SMTP_USER:'user',SMTP_PASS:'password'} as const;
const limits={SMTP_MAX_FREQUENCY:'min_interval_seconds',RATE_LIMIT_EMAIL_SENT:'rate_limit_email_sent'} as const;
const permitted=new Set([...Object.keys(fields),...Object.keys(limits)]);
const view=(smtp:SmtpView)=>({
 SMTP_ADMIN_EMAIL:smtp.enabled?smtp.admin_email:null,SMTP_SENDER_NAME:smtp.enabled?smtp.sender_name:null,
 SMTP_HOST:smtp.enabled?smtp.host:null,SMTP_PORT:smtp.enabled?String(smtp.port):null,
 SMTP_USER:smtp.enabled?smtp.user:null,SMTP_PASS:smtp.password_set?MASK:null,
 SMTP_MAX_FREQUENCY:smtp.min_interval_seconds,RATE_LIMIT_EMAIL_SENT:smtp.rate_limit_email_sent,
 MAILER_AUTOCONFIRM:!smtp.confirm_email,
});

/** The pinned native form's SMTP-only contract. The Studio session supplies the runtime;
 * neither a native project ref nor a request body can select a different environment. */
export function studioSmtpConfig(catalog:Catalog,signInDirectory=PRIVATE_DIRECTORY,mailDirectory=PRIVATE_DIRECTORY,saveInterval=SAVE_INTERVAL_MS){
 return async(actor:string,runtime:string,request:Request,authorized:()=>boolean=()=>true):Promise<Response>=>{
  if(new URL(request.url).pathname!=='/api/platform/auth/default/config')return reply(404,{message:'Unknown auth configuration route'});
  if(!['GET','PATCH'].includes(request.method))return reply(405,{message:'Method not allowed'});
  try{
   const environment=catalog.studioNavigation(actor,runtime).current.environment.id;
   const nativeAuthorized=()=>authorized()&&!request.signal.aborted;
   const current=()=>nativeAuthorized()&&catalog.studioAllowed(actor,runtime)&&catalog.signIn(actor,environment).runtime===runtime;
   if(!current())return reply(403,{message:'Forbidden'});
   const handler=smtpHandler(catalog,async()=>current()?actor:null,signInDirectory,mailDirectory,saveInterval);
   // Keep the original Studio session alive through the reused handler's own awaits.
   // Catalog guards inside that handler recheck membership independently of this pure scope.
   const invoke=(request:Request)=>withManagementAuthorization(nativeAuthorized,()=>handler(request));
   const path='http://management.internal/management/v1/environments/'+environment+'/smtp';
   const get=await invoke(new Request(path));
   if(!get.ok)return get;
   const snapshot=await get.json() as {data:{smtp:SmtpView;revision:number}};
   if(!current())return reply(403,{message:'Forbidden'});
   if(request.method==='GET')return reply(200,view(snapshot.data.smtp));
   if(request.headers.get('content-type')?.split(';')[0]?.trim()!=='application/json')return reply(415,{message:'JSON is required'});
   let input:unknown;try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
   if(!current())return reply(403,{message:'Forbidden'});
   if(!input||typeof input!=='object'||Array.isArray(input))return reply(400,{message:'Invalid SMTP settings'});
   const body=input as Record<string,unknown>;
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
   const response=reply(saved.status,view(result.data.smtp));
   response.headers.set('x-sbarbase-settings-state',result.data.state);
   return response;
  }catch(error){
   return reply(error instanceof Error&&error.message==='Forbidden'?403:409,{message:error instanceof Error&&error.message==='Forbidden'?'Forbidden':'SMTP settings are unavailable'});
  }
 };
}
