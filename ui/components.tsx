import {useId,useRef,useState,type FormEvent,type ReactNode} from 'react';
import {Plus,RefreshCw,Monitor,Sun,Moon} from 'lucide-react';
import {applyTheme,nextTheme,storeTheme,storedTheme,type ThemeMode} from './theme';
import {nameError} from './validation';
const themeLabels:Record<ThemeMode,string>={system:'System theme',light:'Light theme',dark:'Dark theme'};
/** Follows the operating system by default; the button cycles system, light, dark. */
export function ThemeControl(){
 const [mode,setMode]=useState<ThemeMode>(()=>{try{return storedTheme();}catch{return 'system';}});
 const Icon=mode==='system'?Monitor:mode==='light'?Sun:Moon;
 return <button className="theme-control" type="button" aria-label={themeLabels[mode]+', change theme'} title={themeLabels[mode]+', change theme'} onClick={()=>{const next=nextTheme(mode);try{storeTheme(next);}catch{}applyTheme(next);setMode(next);}}><Icon aria-hidden="true"/>{themeLabels[mode]}</button>;
}
export function ErrorMessage({message}:{message:string}){return message?<p className="error" role="alert">{message}</p>:null;}
export function Loading(){return <p className="muted" role="status">Loading…</p>;}
export function Empty({children}:{children:ReactNode}){return <div className="empty">{children}</div>;}
export function Refresh({onClick}:{onClick:()=>void}){return <button className="secondary" onClick={onClick}><RefreshCw aria-hidden="true"/>Refresh</button>;}
export function NameForm({label,onSubmit,onCancel,action='Create'}:{label:string;onSubmit:(name:string)=>Promise<void>;onCancel:()=>void;action?:string}) {
 const id=useId(),input=useRef<HTMLInputElement>(null),[name,setName]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[invalid,setInvalid]=useState('');
 async function submit(event:FormEvent){event.preventDefault();if(busy)return;setError('');const issue=nameError(name);setInvalid(issue);
  if(issue){input.current?.focus();return;}setBusy(true);
  try{await onSubmit(name.trim());}catch(e){setError(e instanceof Error?e.message:'Unable to save the name.');}finally{setBusy(false);}}
 return <form className="create-form" onSubmit={submit} noValidate aria-busy={busy}><label htmlFor={id}>{label}</label><div className="form-row"><input ref={input} id={id} value={name} autoFocus required disabled={busy} onChange={event=>{const value=event.target.value;setName(value);if(invalid)setInvalid(nameError(value));}} onBlur={()=>setInvalid(nameError(name))} aria-invalid={invalid?true:undefined} aria-describedby={invalid?id+'-error':undefined}/><button className="primary" disabled={busy}>{action==='Create'&&<Plus aria-hidden="true"/>}{busy?(action==='Create'?'Creating…':'Saving…'):action}</button><button type="button" onClick={onCancel} disabled={busy}>Cancel</button></div>{invalid&&<p className="field-error" id={id+'-error'} role="alert">{invalid}</p>}<ErrorMessage message={error}/></form>;
}
