import {useId,useState,type FormEvent,type ReactNode} from 'react';
import {Plus,RefreshCw} from 'lucide-react';
export function ErrorMessage({message}:{message:string}){return message?<p className="error" role="alert">{message}</p>:null;}
export function Loading(){return <p className="muted" role="status">Loading…</p>;}
export function Empty({children}:{children:ReactNode}){return <div className="empty">{children}</div>;}
export function Refresh({onClick}:{onClick:()=>void}){return <button className="secondary" onClick={onClick}><RefreshCw aria-hidden="true"/>Refresh</button>;}
export function NameForm({label,onSubmit,onCancel}:{label:string;onSubmit:(name:string)=>Promise<void>;onCancel:()=>void}) {
 const id=useId(),[name,setName]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function submit(event:FormEvent){event.preventDefault();setBusy(true);setError('');try{await onSubmit(name.trim());}catch(e){setError(e instanceof Error?e.message:'Unable to create.');}finally{setBusy(false);}}
 return <form className="create-form" onSubmit={submit} aria-busy={busy}><label htmlFor={id}>{label}</label><div className="form-row"><input id={id} value={name} autoFocus required maxLength={100} onChange={event=>setName(event.target.value)} aria-describedby={error?id+'-error':undefined}/><button className="primary" disabled={busy||!name.trim()}><Plus aria-hidden="true"/>{busy?'Creating…':'Create'}</button><button type="button" onClick={onCancel} disabled={busy}>Cancel</button></div><div id={id+'-error'}><ErrorMessage message={error}/></div></form>;
}
